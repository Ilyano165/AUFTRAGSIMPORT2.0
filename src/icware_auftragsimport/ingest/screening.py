"""Vorprüfung einer Mail vor dem Laden: Spam-Markierung und automatische Antworten.

Grundsatz: Es zählt nur, was der Mailserver selbst markiert hat (Spamfilter-Kopfzeilen,
Junk-Markierungen). Eine eigene inhaltliche Spam-Bewertung gibt es bewusst nicht: Ein
fälschlich aussortierter Auftrag kostet mehr als eine Werbemail in der Prüfliste.

Automatisch erzeugte Mails werden nur übersprungen, wenn sie eindeutig Antworten sind
(Abwesenheitsnotiz, Zustellbericht). ``Auto-Submitted: auto-generated`` und
``Precedence: bulk`` bleiben erlaubt: Bestellbenachrichtigungen aus Webshops tragen sie oft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import policy, utils
from email.header import decode_header
from email.message import Message
from email.parser import BytesHeaderParser
from enum import StrEnum

from .imap import Envelope

HEADER_BYTES = 64 * 1024
SPAM_FLAGS = frozenset({"$junk", "junk", "\\junk"})
NOT_JUNK_FLAGS = frozenset({"$notjunk", "notjunk", "nonjunk"})
_SPAM_SUBJECT = re.compile(r"^\s*(?:\[\s*spam\s*\]|\*{2,}\s*spam\s*\*{2,}|\{\s*spam\s*\})", re.I)
_SCL = re.compile(r"\bSCL:\s*(-?\d+)", re.I)
_BOUNCE_SENDERS = ("mailer-daemon@", "postmaster@")


class Verdict(StrEnum):
    """Ergebnis der Vorprüfung."""

    ACCEPT = "accept"
    SPAM = "spam"
    AUTO_REPLY = "auto_reply"


@dataclass(frozen=True, slots=True)
class Screening:
    """Ergebnis mit technischem Grund (ohne Mailinhalt, nur für Protokoll und Tests)."""

    verdict: Verdict
    reason: str = ""


def read_headers(header: bytes) -> Message:
    """Kopfzeilen fehlertolerant; kaputte Kopfdaten ergeben eine leere Nachricht."""
    try:
        return BytesHeaderParser(policy=policy.compat32).parsebytes(header[:HEADER_BYTES])
    except Exception:
        return Message()


def _text(headers: Message, name: str) -> str:
    value = headers.get(name)
    return str(value).strip() if value is not None else ""


def _subject(headers: Message) -> str:
    raw = _text(headers, "Subject")
    try:
        return "".join(
            chunk.decode(charset or "utf-8", "replace") if isinstance(chunk, bytes) else chunk
            for chunk, charset in decode_header(raw)
        )
    except Exception:
        return raw


def sender_address(headers: Message) -> str:
    """Absenderadresse in Kleinbuchstaben oder leer, wenn sie fehlt oder mehrdeutig ist."""
    pairs = [p for p in utils.getaddresses([_text(headers, "From")]) if p != ("", "")]
    if len(pairs) != 1 or "@" not in pairs[0][1]:
        return ""
    return pairs[0][1].strip().lower()


def _spam_reason(envelope: Envelope, headers: Message) -> str:
    forefront = _text(headers, "X-Forefront-Antispam-Report")
    scl = _SCL.search(forefront)
    exchange_scl = _text(headers, "X-MS-Exchange-Organization-SCL")
    checks = (
        (bool({flag.casefold() for flag in envelope.flags} & SPAM_FLAGS), "Junk-Markierung"),
        (_text(headers, "X-Spam-Flag").lower() == "yes", "X-Spam-Flag"),
        (_text(headers, "X-Spam-Status").lower().startswith("yes"), "X-Spam-Status"),
        (_text(headers, "X-Spam").lower() in ("yes", "true"), "X-Spam"),
        ("SFV:SPM" in forefront.upper() or bool(scl and int(scl.group(1)) >= 5), "Microsoft"),
        (exchange_scl.lstrip("-").isdigit() and int(exchange_scl) >= 5, "Microsoft SCL"),
        (bool(_SPAM_SUBJECT.match(_subject(headers))), "Spam-Kennzeichnung im Betreff"),
    )
    return next((reason for hit, reason in checks if hit), "")


def _auto_reason(headers: Message) -> str:
    if _text(headers, "Auto-Submitted").lower().startswith("auto-replied"):
        return "Auto-Submitted: auto-replied"
    if headers.get("X-Autoreply") is not None or headers.get("X-Autorespond") is not None:
        return "Abwesenheitsnotiz"
    content_type = _text(headers, "Content-Type").lower()
    if content_type.startswith("multipart/report"):
        return "Zustellbericht"
    if sender_address(headers).startswith(_BOUNCE_SENDERS):
        return "Zustellbericht"
    return ""


def screen(envelope: Envelope) -> Screening:
    """Bewertet eine Mail anhand von Markierungen und Kopfzeilen."""
    headers = read_headers(envelope.header)
    not_junk = bool({flag.casefold() for flag in envelope.flags} & NOT_JUNK_FLAGS)
    spam = "" if not_junk else _spam_reason(envelope, headers)
    if spam:
        return Screening(Verdict.SPAM, spam)
    automatic = _auto_reason(headers)
    if automatic:
        return Screening(Verdict.AUTO_REPLY, automatic)
    return Screening(Verdict.ACCEPT)
