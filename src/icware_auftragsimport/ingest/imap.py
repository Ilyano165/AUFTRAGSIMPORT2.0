"""IMAP-Zugriff ausschließlich über TLS mit Zertifikats- und Hostnamenprüfung.

- Implizites TLS (Port 993) oder STARTTLS; unverschlüsselte Verbindungen sind unmöglich.
- Die Zertifikatsprüfung lässt sich nicht abschalten (keine Option dafür).
- Feste Timeouts; Wiederholung mit exponentiellem Backoff nur bei Netzwerkfehlern,
  nie bei Zertifikats-, TLS- oder Anmeldefehlern.
- Mails werden erst nach Größenprüfung und nur bis zum Limit geladen.
- Das Passwort wird vor jedem Fehlertext entfernt und nie protokolliert.
- Ordner werden nur lesend geöffnet (``EXAMINE``) und Mails nur mit ``BODY.PEEK`` geladen:
  Der Abruf setzt weder „gelesen“ noch andere Markierungen und verschiebt nichts.
"""

from __future__ import annotations

import base64
import imaplib
import random
import re
import socket
import ssl
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from ..config.schema import MailAccount, TlsMode
from ..domain.errors import UserMessage
from ..infrastructure.logging_setup import get_logger
from ..security.redaction import redact, register_secret

DEFAULT_TIMEOUT = 30.0
MAX_ATTEMPTS = 4
BACKOFF_BASE = 2.0
BACKOFF_MAX = 60.0
ENVELOPE_BATCH = 100
_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")
_UID = re.compile(rb"UID (\d+)")
_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_FETCH_START = re.compile(rb"^\d+ \(")
_log = get_logger("imap")
T = TypeVar("T")


class ImapErrorKind(StrEnum):
    """Fehlerart; bestimmt, ob ein neuer Versuch sinnvoll ist."""

    TLS = "tls"
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    PROTOCOL = "protocol"
    OVERSIZED = "oversized"
    TIMEOUT = "timeout"
    MAILBOX = "mailbox"


RETRYABLE = frozenset({ImapErrorKind.NETWORK, ImapErrorKind.TIMEOUT})


class ImapError(Exception):
    """IMAP-Fehler mit bereinigter Benutzermeldung."""

    def __init__(self, kind: ImapErrorKind, message: UserMessage) -> None:
        super().__init__(message.render())
        self.kind = kind
        self.message = message


class ImapTransport(Protocol):
    """Benötigter Ausschnitt von ``imaplib.IMAP4``."""

    def login(self, user: str, password: str) -> tuple[str, list[Any]]: ...
    def select(self, mailbox: str = ..., readonly: bool = ...) -> tuple[str, list[Any]]: ...
    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]: ...
    def logout(self) -> tuple[str, list[Any]]: ...


Factory = Callable[[str, int, ssl.SSLContext, float, TlsMode], ImapTransport]
RetryNotice = Callable[[int, ImapErrorKind, float], None]


@dataclass(frozen=True, slots=True)
class MailboxInfo:
    """Geöffneter Ordner: UIDVALIDITY (Gültigkeit der UIDs) und Anzahl Nachrichten."""

    folder: str
    uidvalidity: int | None
    exists: int


@dataclass(frozen=True, slots=True)
class Envelope:
    """Was vor dem Laden einer Mail bekannt ist: UID, Größe, Markierungen, Kopfzeilen."""

    uid: int
    size: int
    flags: frozenset[str]
    header: bytes


def encode_mailbox(name: str) -> str:
    """Ordnername für IMAP: modifiziertes UTF-7 (RFC 3501, 5.1.3), in Anführungszeichen.

    ``Aufträge`` wird zu ``"Auftr&AOQ-ge"``; Leerzeichen und Anführungszeichen sind geschützt.
    """
    parts: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            data = "".join(pending).encode("utf-16-be")
            text = base64.b64encode(data).decode("ascii").rstrip("=").replace("/", ",")
            parts.append(f"&{text}-")
            pending.clear()

    for char in name:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            parts.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    encoded = "".join(parts).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{encoded}"'


def parse_envelopes(data: Sequence[object]) -> list[Envelope]:
    """Wertet eine FETCH-Antwort aus; FLAGS dürfen vor oder nach dem Kopf-Literal stehen."""
    records: list[tuple[bytes, bytes]] = []
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[0], bytes):
            literal = item[1] if isinstance(item[1], bytes) else b""
            records.append((item[0], literal))
        elif isinstance(item, bytes):
            if _FETCH_START.match(item):
                records.append((item, b""))
            elif records:
                meta, literal = records[-1]
                records[-1] = (meta + item, literal)
    envelopes = []
    for meta, literal in records:
        uid, size = _UID.search(meta), _SIZE.search(meta)
        if uid is None or size is None:
            continue
        flags = _FLAGS.search(meta)
        names = (
            frozenset(flags.group(1).decode("ascii", "replace").split()) if flags else frozenset()
        )
        envelopes.append(Envelope(int(uid.group(1)), int(size.group(1)), names, literal))
    return envelopes


def tls_context() -> ssl.SSLContext:
    """TLS ab 1.2 mit Zertifikats- und Hostnamenprüfung gegen den Systemspeicher."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def default_factory(
    host: str, port: int, context: ssl.SSLContext, timeout: float, mode: TlsMode
) -> ImapTransport:
    """Baut die Verbindung; STARTTLS muss vor jeder Anmeldung gelingen."""
    if mode is TlsMode.IMPLICIT:
        return imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=timeout)
    connection = imaplib.IMAP4(host, port, timeout=timeout)
    try:
        connection.starttls(ssl_context=context)
    except BaseException:
        connection.shutdown()
        raise
    return connection


def socket_is_tls(connection: object) -> bool:
    """True, wenn die Verbindung über einen TLS-Socket läuft."""
    return isinstance(getattr(connection, "sock", None), ssl.SSLSocket)


def _message(what: str, why: str, action: str) -> UserMessage:
    return UserMessage(
        what=what, why=why, unchanged="Im Postfach wurde nichts verändert", action=action
    )


def classify(exc: BaseException, secret: str = "") -> ImapError:
    """Ordnet Ausnahmen Fehlerarten zu; Texte werden bereinigt."""
    detail = redact(str(exc).replace(secret, "<entfernt>") if secret else str(exc))[:300]
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ImapError(
            ImapErrorKind.TLS,
            _message(
                "Das Zertifikat des Mailservers ist ungültig",
                detail,
                "Servername und Zertifikat prüfen; die Prüfung wird nicht abgeschaltet",
            ),
        )
    if isinstance(exc, ssl.SSLError):
        return ImapError(
            ImapErrorKind.TLS,
            _message(
                "TLS-Verbindung fehlgeschlagen", detail, "TLS-Einstellungen des Servers prüfen"
            ),
        )
    timed_out = isinstance(exc, TimeoutError) or (
        isinstance(exc, imaplib.IMAP4.abort) and "timed out" in str(exc).lower()
    )
    if timed_out:
        return ImapError(
            ImapErrorKind.TIMEOUT,
            _message(
                "Der Mailserver antwortet nicht rechtzeitig",
                detail,
                "Netzwerk prüfen; es wird erneut versucht",
            ),
        )
    if isinstance(
        exc, imaplib.IMAP4.abort | socket.timeout | TimeoutError | ConnectionError | OSError
    ):
        return ImapError(
            ImapErrorKind.NETWORK,
            _message(
                "Mailserver nicht erreichbar", detail, "Netzwerk prüfen; es wird erneut versucht"
            ),
        )
    return ImapError(
        ImapErrorKind.PROTOCOL,
        _message("Mailserver meldet einen Fehler", detail, "Kontoeinstellungen prüfen"),
    )


class SecureImapClient:
    """Verbindung mit kontrolliertem Wiederaufbau."""

    def __init__(
        self,
        account: MailAccount,
        password: Callable[[], str],
        *,
        factory: Factory = default_factory,
        is_encrypted: Callable[[object], bool] = socket_is_tls,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        on_retry: RetryNotice | None = None,
    ) -> None:
        self._account = account
        self._password = password
        self._factory = factory
        self._is_encrypted = is_encrypted
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._jitter = jitter
        self._on_retry = on_retry
        self._connection: ImapTransport | None = None
        self._mailbox: str | None = None
        self.mailbox_info: MailboxInfo | None = None

    def _connect(self) -> ImapTransport:
        account = self._account
        connection = self._factory(
            account.host, account.port, tls_context(), self._timeout, account.tls_mode
        )
        if not self._is_encrypted(connection):
            raise ImapError(
                ImapErrorKind.TLS,
                _message(
                    "Verbindung ist nicht verschlüsselt",
                    "TLS wurde nicht aufgebaut",
                    "TLS-Modus und Port des Kontos prüfen",
                ),
            )
        secret = self._password()
        register_secret(secret)
        try:
            connection.login(account.username, secret)
        except imaplib.IMAP4.abort as exc:
            raise classify(exc, secret) from None
        except imaplib.IMAP4.error as exc:
            detail = redact(str(exc).replace(secret, "<entfernt>"))[:300]
            raise ImapError(
                ImapErrorKind.AUTHENTICATION,
                _message(
                    "Anmeldung am Mailserver fehlgeschlagen",
                    detail,
                    "Benutzername und Passwort in der Windows-Anmeldeinformationsverwaltung prüfen",
                ),
            ) from None
        if self._mailbox is not None:
            self.mailbox_info = self._select(connection, self._mailbox)
        return connection

    def _select(self, connection: ImapTransport, folder: str) -> MailboxInfo:
        """Öffnet den Ordner nur lesend; auch nach einem Wiederaufbau der Verbindung."""
        status, data = connection.select(encode_mailbox(folder), readonly=True)
        if status != "OK":
            raise ImapError(
                ImapErrorKind.MAILBOX,
                _message(
                    "Der Postfachordner ist nicht erreichbar",
                    f"Ordner „{folder}“: {redact(str(data[:1]))[:120]}",
                    "Ordnernamen in den Einstellungen des Postfachs prüfen",
                ),
            )
        exists = int(data[0]) if data and isinstance(data[0], bytes) and data[0].isdigit() else 0
        uidvalidity: int | None = None
        response = getattr(connection, "response", None)
        if callable(response):
            _, values = response("UIDVALIDITY")
            first = values[0] if values else None
            if isinstance(first, bytes) and first.isdigit():
                uidvalidity = int(first)
        return MailboxInfo(folder, uidvalidity, exists)

    def open_mailbox(self, folder: str) -> MailboxInfo:
        """Meldet an und öffnet den Ordner lesend; Grundlage aller weiteren Befehle."""
        self._mailbox = folder

        def operation(connection: ImapTransport) -> MailboxInfo:
            if self.mailbox_info is None or self.mailbox_info.folder != folder:
                self.mailbox_info = self._select(connection, folder)
            return self.mailbox_info

        return self.run(operation)

    def search_uids(self) -> list[int]:
        """UIDs aller nicht gelöschten Nachrichten, aufsteigend.

        Bewusst nicht nur ungelesene: Öffnet jemand die Mail in Outlook, wäre sie sonst
        für den Import unsichtbar. Bekannte UIDs überspringt der Abruf ohne Download.
        """

        def operation(connection: ImapTransport) -> list[int]:
            status, data = connection.uid("SEARCH", "UNDELETED")
            if status != "OK":
                raise ImapError(
                    ImapErrorKind.PROTOCOL,
                    _message("Ordnerinhalt nicht lesbar", status, "Später erneut versuchen"),
                )
            text = b" ".join(d for d in data if isinstance(d, bytes))
            return sorted({int(part) for part in text.split() if part.isdigit()})

        return self.run(operation)

    def envelopes(self, uids: Sequence[int], header_bytes: int) -> list[Envelope]:
        """Größe, Markierungen und Kopfzeilen (gekürzt) in Paketen, ohne die Mails zu laden."""
        found: list[Envelope] = []
        items = f"(UID RFC822.SIZE FLAGS BODY.PEEK[HEADER]<0.{header_bytes}>)"
        for start in range(0, len(uids), ENVELOPE_BATCH):
            chunk = ",".join(str(uid) for uid in uids[start : start + ENVELOPE_BATCH])

            def operation(connection: ImapTransport, chunk: str = chunk) -> list[Envelope]:
                status, data = connection.uid("FETCH", chunk, items)
                if status != "OK":
                    raise ImapError(
                        ImapErrorKind.PROTOCOL,
                        _message("Kopfdaten nicht lesbar", status, "Später erneut versuchen"),
                    )
                return parse_envelopes(data)

            found += self.run(operation)
        return found

    def run(self, operation: Callable[[ImapTransport], T]) -> T:
        """Führt eine Operation aus; baut die Verbindung bei Netzwerkfehlern neu auf."""
        last: ImapError | None = None
        for attempt in range(self._max_attempts):
            try:
                if self._connection is None:
                    self._connection = self._connect()
                return operation(self._connection)
            except ImapError as exc:
                error = exc
            except Exception as exc:
                error = classify(exc)
            self.close()
            if error.kind not in RETRYABLE:
                raise error
            last = error
            delay = min(BACKOFF_MAX, BACKOFF_BASE * 2**attempt) * (0.5 + self._jitter() / 2)
            if self._on_retry is not None and attempt + 1 < self._max_attempts:
                self._on_retry(attempt + 1, error.kind, delay)
            _log.warning(
                "IMAP %s: Versuch %d fehlgeschlagen (%s)",
                self._account.id,
                attempt + 1,
                error.kind.value,
            )
            if attempt + 1 < self._max_attempts:
                self._sleep(delay)
        assert last is not None
        raise last

    def message_size(self, uid: str) -> int:
        """Größe laut Server, ohne die Mail zu laden."""

        def operation(connection: ImapTransport) -> int:
            status, data = connection.uid("FETCH", uid, "(RFC822.SIZE)")
            match = _SIZE.search(b" ".join(d for d in data if isinstance(d, bytes)))
            if status != "OK" or match is None:
                raise ImapError(
                    ImapErrorKind.PROTOCOL, _message("Größe nicht lesbar", status, "Später erneut")
                )
            return int(match.group(1))

        return self.run(operation)

    def fetch(self, uid: str, max_bytes: int, *, size: int | None = None) -> bytes:
        """Lädt eine Mail höchstens bis ``max_bytes``; größere Mails werden nicht geladen.

        ``size`` aus einer vorherigen Kopfdatenabfrage spart eine Rundreise zum Server.
        """
        if (self.message_size(uid) if size is None else size) > max_bytes:
            raise ImapError(
                ImapErrorKind.OVERSIZED,
                _message(
                    "Mail überschreitet das Größenlimit",
                    f"mehr als {max_bytes} Byte",
                    "Mail im Mailprogramm prüfen; sie bleibt unverändert im Postfach",
                ),
            )

        def operation(connection: ImapTransport) -> bytes:
            status, data = connection.uid("FETCH", uid, f"(BODY.PEEK[]<0.{max_bytes + 1}>)")
            literal = next(
                (item[1] for item in data if isinstance(item, tuple) and len(item) > 1), None
            )
            if status != "OK" or not isinstance(literal, bytes):
                raise ImapError(
                    ImapErrorKind.PROTOCOL, _message("Mail nicht lesbar", status, "Später erneut")
                )
            if len(literal) > max_bytes:
                raise ImapError(
                    ImapErrorKind.OVERSIZED, _message("Mail zu groß", "Limit überschritten", "-")
                )
            return literal

        return self.run(operation)

    def abort(self) -> None:
        """Bricht eine laufende Operation aus einem anderen Thread ab (Socket schließen)."""
        shutdown = getattr(self._connection, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                _log.debug("IMAP-Verbindung beim Abbruch bereits geschlossen")

    def close(self) -> None:
        """Beendet die Verbindung, Fehler beim Abmelden werden ignoriert."""
        self.mailbox_info = None
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                _log.debug("IMAP-Abmeldung fehlgeschlagen")
