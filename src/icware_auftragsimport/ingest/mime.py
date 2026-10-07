"""Gehärtetes Einlesen einer Rohmail.

Jede Mail gilt als potenziell fehlerhaft oder bösartig. Ablauf:
1. Vorprüfung der Bytes: Gesamtgröße, Kopfbereich, Anzahl MIME-Grenzen
2. Parsen mit ``policy.compat32``: keine komplexe Header-Auswertung während des Parsens
3. Iterativer Durchlauf der Teile mit Tiefen- und Anzahlgrenze (keine Rekursion)
4. Jeder Header einzeln, fehlertolerant dekodiert, bereinigt (CR/LF, Steuer- und
   Bidi-Zeichen) und gekürzt; Absender strikt geprüft
5. Text mit erlaubten Zeichensätzen dekodiert, HTML nur in Text umgewandelt
6. Anhänge nur geprüft und gehasht; ihr Name dient nie als Pfad
Jeder Fehler führt zu ``MailRejected`` mit Code, nie zu einem Absturz.
"""

from __future__ import annotations

import codecs
import hashlib
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy, utils
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser

from ..parsers.text import html_to_text
from ..security.limits import ATTACHMENTS, MAIL, AttachmentLimits, MailLimits
from ..security.sanitize import clean_untrusted
from .attachments import AttachmentReport, Verdict, inspect_attachment

_ADDRESS = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_MESSAGE_ID = re.compile(r"<[\x21-\x3b\x3d\x3f-\x7e]{1,250}>")
_ALLOWED_CHARSETS = ("utf_8", "utf_16", "ascii", "latin_1", "iso8859", "cp125", "cp437",
                     "cp850", "cp852", "koi8", "mac_")  # fmt: skip
_STRICT_ADDRESSES = "strict" in inspect.signature(utils.getaddresses).parameters


class MailRejected(Exception):
    """Mail wird nicht verarbeitet; Rohdaten bleiben zur Prüfung erhalten."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class MailAttachment:
    """Geprüfter Anhang; ``data`` nur bei nicht gesperrten Anhängen."""

    report: AttachmentReport
    declared_type: str
    data: bytes = field(repr=False, default=b"")


@dataclass(frozen=True, slots=True)
class ParsedMail:
    """Bereinigtes Ergebnis."""

    raw_sha256: str
    message_id: str
    sender: str
    sender_name: str
    subject: str
    date: datetime | None
    text: str
    html_text: str
    attachments: tuple[MailAttachment, ...]
    defects: tuple[str, ...]
    truncated: bool


@dataclass(slots=True)
class _State:
    limits: MailLimits
    attachment_limits: AttachmentLimits
    text: list[str] = field(default_factory=list)
    html: list[str] = field(default_factory=list)
    text_chars: int = 0
    attachments: list[MailAttachment] = field(default_factory=list)
    attachment_bytes: int = 0
    defects: list[str] = field(default_factory=list)
    truncated: bool = False


def parse_mail(
    raw: bytes, limits: MailLimits = MAIL, attachment_limits: AttachmentLimits = ATTACHMENTS
) -> ParsedMail:
    """Liest eine Rohmail sicher ein oder wirft ``MailRejected``."""
    try:
        _prescan(raw, limits)
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
        return _evaluate(raw, message, _State(limits, attachment_limits))
    except MailRejected:
        raise
    except Exception as exc:
        raise MailRejected(
            "MAIL_UNPARSEABLE", f"Mail nicht auswertbar ({type(exc).__name__})"
        ) from exc


def _prescan(raw: bytes, limits: MailLimits) -> None:
    if len(raw) > limits.max_message_bytes:
        raise MailRejected("MAIL_TOO_LARGE", f"Mail größer als {limits.max_message_bytes} Byte")
    ends = [i for i in (raw.find(b"\r\n\r\n"), raw.find(b"\n\n")) if i >= 0]
    header_end = min(ends) if ends else len(raw)
    if header_end > limits.max_header_block_bytes:
        raise MailRejected("MAIL_HEADER_TOO_LARGE", "Kopfbereich zu groß")
    if raw.count(b"\n--") > limits.max_boundary_lines:
        raise MailRejected("MAIL_TOO_MANY_PARTS", "Zu viele MIME-Grenzen")


def _decode_words(value: str) -> str:
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(_decode_bytes(chunk, charset)[0])
        else:
            parts.append(chunk)
    return "".join(parts)


def _header(message: Message, name: str, state: _State) -> str:
    values = message.get_all(name) or []
    if not values:
        return ""
    if len(values) > 1 and name in ("From", "Message-ID", "Date", "Subject"):
        state.defects.append(f"Header {name} mehrfach vorhanden")
    raw = str(values[0])
    try:
        decoded = _decode_words(raw)
    except Exception:
        state.defects.append(f"Header {name} nicht dekodierbar")
        decoded = raw
    cleaned = clean_untrusted(decoded, max_chars=state.limits.max_header_value_chars)
    if len(decoded) > state.limits.max_header_value_chars:
        state.defects.append(f"Header {name} gekürzt")
    return cleaned


def _sender(message: Message, state: _State) -> tuple[str, str]:
    raw = clean_untrusted(
        str(message.get("From", "")), max_chars=state.limits.max_header_value_chars
    )
    kwargs = {"strict": True} if _STRICT_ADDRESSES else {}
    pairs = [p for p in utils.getaddresses([raw], **kwargs) if p != ("", "")]
    if len(pairs) != 1 or not _ADDRESS.fullmatch(pairs[0][1]):
        state.defects.append("Absender fehlt, ist mehrdeutig oder ungültig")
        return "", ""
    name, address = pairs[0]
    try:
        name = _decode_words(name)
    except Exception:
        state.defects.append("Absendername nicht dekodierbar")
    return address.lower(), clean_untrusted(name, max_chars=200)


def _decode_bytes(payload: bytes, charset: str | None) -> tuple[str, bool]:
    """Text und ob ein Ersatzzeichensatz nötig war."""
    name = (charset or "utf-8").strip().lower()
    try:
        codec = codecs.lookup(name).name.replace("-", "_")
    except LookupError:
        codec = ""
    if codec.startswith(_ALLOWED_CHARSETS):
        try:
            return payload.decode(codec), False
        except UnicodeDecodeError:
            pass
    try:
        return payload.decode("utf-8"), name not in ("utf-8", "utf8", "us-ascii", "ascii")
    except UnicodeDecodeError:
        return payload.decode("cp1252", errors="replace"), True


def _evaluate(raw: bytes, message: Message, state: _State) -> ParsedMail:
    if len(message.keys()) > state.limits.max_headers:
        raise MailRejected("MAIL_TOO_MANY_HEADERS", "Zu viele Header")
    state.defects.extend(f"MIME-Fehler: {type(d).__name__}" for d in message.defects[:20])
    digest = hashlib.sha256(raw).hexdigest()
    message_id = _header(message, "Message-ID", state)
    if not _MESSAGE_ID.fullmatch(message_id):
        state.defects.append("Message-ID fehlt oder ist ungültig; Ersatzkennung aus Inhalt")
        message_id = f"<sha256-{digest[:40]}@icware.invalid>"
    sender, sender_name = _sender(message, state)
    subject = _header(message, "Subject", state)
    date = _date(_header(message, "Date", state), state)
    _walk(message, state)
    text = "\n".join(state.text)
    return ParsedMail(
        raw_sha256=digest, message_id=message_id, sender=sender, sender_name=sender_name,
        subject=subject, date=date, text=text, html_text="\n".join(state.html),
        attachments=tuple(state.attachments), defects=tuple(dict.fromkeys(state.defects)),
        truncated=state.truncated,
    )  # fmt: skip


def _date(value: str, state: _State) -> datetime | None:
    if not value:
        return None
    try:
        return utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        state.defects.append("Datum nicht lesbar")
        return None


def _walk(root: Message, state: _State) -> None:
    stack: list[tuple[Message, int, bool]] = [(root, 0, False)]
    parts = 0
    while stack:
        part, depth, embedded = stack.pop()
        parts += 1
        if parts > state.limits.max_parts:
            raise MailRejected("MAIL_TOO_MANY_PARTS", "Zu viele MIME-Teile")
        if depth > state.limits.max_depth:
            raise MailRejected("MAIL_TOO_DEEP", "MIME-Struktur zu tief verschachtelt")
        payload = part.get_payload()
        if part.is_multipart() and isinstance(payload, list):
            inner = embedded or part.get_content_type() == "message/rfc822"
            children = (child for child in reversed(payload) if isinstance(child, Message))
            stack.extend((child, depth + 1, inner) for child in children)
            continue
        _leaf(part, state, embedded)


def _leaf(part: Message, state: _State, embedded: bool) -> None:
    content_type = part.get_content_type()
    disposition = part.get_content_disposition()
    try:
        filename = part.get_filename()
    except Exception:
        filename = "anhang"
        state.defects.append("Anhangname nicht dekodierbar")
    encoded = part.get_payload()
    encoded_len = len(encoded) if isinstance(encoded, str | bytes) else 0
    is_body = (
        not embedded and disposition != "attachment" and not filename
        and content_type in ("text/plain", "text/html")
    )  # fmt: skip
    if not is_body:
        _attachment(part, filename, content_type, encoded_len, state)
        return
    limit = (
        state.limits.max_html_bytes
        if content_type == "text/html"
        else state.limits.max_text_chars * 4
    )
    if encoded_len > limit * 2:
        state.truncated = True
        state.defects.append(f"{content_type}-Teil zu groß, nicht ausgewertet")
        return
    data = part.get_payload(decode=True)
    if not isinstance(data, bytes):
        return
    text, fallback = _decode_bytes(data, part.get_content_charset())
    if fallback:
        state.defects.append("Zeichensatz unbekannt oder fehlerhaft; Ersatzdekodierung")
    if content_type == "text/html":
        try:
            text = html_to_text(text[: state.limits.max_html_bytes])
        except Exception:
            state.defects.append("HTML nicht auswertbar")
            return
        target = state.html
    else:
        target = state.text
    room = state.limits.max_text_chars - state.text_chars
    if len(text) > room:
        text, state.truncated = text[: max(room, 0)], True
    state.text_chars += len(text)
    target.append(text)


def _attachment(
    part: Message, filename: str | None, content_type: str, encoded_len: int, state: _State
) -> None:
    limits = state.limits
    if len(state.attachments) >= limits.max_attachments:
        state.defects.append("Weitere Anhänge über dem Limit wurden ignoriert")
        return
    estimate = encoded_len * 3 // 4
    over = (
        estimate > limits.max_attachment_bytes
        or state.attachment_bytes + estimate > limits.max_total_attachment_bytes
    )
    data = b"" if over else part.get_payload(decode=True)
    if not isinstance(data, bytes):
        data = b""
    if over or len(data) > limits.max_attachment_bytes:
        report = inspect_attachment(filename, b"", state.attachment_limits)
        reason = ("Anhang überschreitet das Größenlimit",)
        blocked = AttachmentReport(
            report.safe_name, report.extension, report.kind, Verdict.BLOCKED, estimate, "", reason
        )
        state.attachments.append(MailAttachment(blocked, content_type))
        return
    state.attachment_bytes += len(data)
    fallback_name = f"anhang.{content_type.rsplit('/', maxsplit=1)[-1]}"
    report = inspect_attachment(filename or fallback_name, data, state.attachment_limits)
    kept = data if report.verdict is not Verdict.BLOCKED else b""
    state.attachments.append(MailAttachment(report, content_type, kept))
