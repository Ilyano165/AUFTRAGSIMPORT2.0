"""Anwendungsdienst „Aufträge abrufen“.

Ablauf je Postfach::

    verbinden → auflisten → bekannte UIDs aussortieren → Kopfdaten → Vorprüfung
    → laden → MIME einlesen → Text wählen → erkennen → Duplikate prüfen → validieren
    → speichern (eine Transaktion je Mail)

Regeln:

- Der Abruf verändert das Postfach nie: kein „gelesen“, kein Verschieben, kein Löschen.
  Archiviert wird erst im späteren, kontrollierten Exportablauf.
- Jede Mail wird in genau einer Transaktion gespeichert (Mail, Auftrag, Idempotenzschlüssel,
  Journal). Ein Fehler oder Abbruch hinterlässt nie halbe Daten.
- Abbruch wird nur zwischen zwei Mails wirksam, nie in einer Transaktion.
- Fehler einer einzelnen Mail werden gezählt und protokolliert; der Abruf läuft weiter.
  Verbindungs-, Anmelde-, TLS- und Datenbankfehler beenden den Abruf sauber.
- Protokoll und Zusammenfassung enthalten nie Mailinhalte, Betreffzeilen oder Absender.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from ..config.schema import AuthMethod, ImportProfile, MailAccount, SenderAction, Settings
from ..domain.errors import AppError, CredentialError
from ..domain.models import MailMetadata, Order
from ..domain.ports import Clock
from ..domain.status import MailState, OrderStatus
from ..infrastructure.db import transaction
from ..infrastructure.logging_setup import get_logger
from ..infrastructure.repositories import (
    IdempotencyRepository,
    JournalRepository,
    MailRepository,
    OrderRepository,
)
from ..ingest.attachments import Verdict as AttachmentVerdict
from ..ingest.imap import Envelope, ImapError, ImapErrorKind, MailboxInfo
from ..ingest.mime import MailRejected, ParsedMail, parse_mail
from ..ingest.screening import Verdict, read_headers, screen, sender_address
from ..ingest.sources import MailSource
from ..parsers.common import ParseContext
from ..parsers.engine import ExtractionEngine
from ..parsers.text import normalize
from ..security.limits import MAIL, MailLimits
from .idempotency import MailDuplicate, MailIdentity, check_mail, content_fingerprint, mail_keys
from .matching import ArticleMatcher, Catalog
from .order_factory import build_order
from .order_review import review_order
from .profile_rules import evaluate_sender
from .validation import status_after_validation

_log = get_logger("abruf")
PLACEHOLDER_TEXT_CHARS = 200


class FetchErrorKind(StrEnum):
    """Fehlerarten; Mail-Fehler betreffen eine Mail, alle anderen beenden den Abruf."""

    CONFIGURATION = "configuration"
    CREDENTIALS = "credentials"
    UNSUPPORTED_AUTH = "unsupported_auth"
    CONNECTION = "connection"
    AUTHENTICATION = "authentication"
    TLS = "tls"
    TIMEOUT = "timeout"
    MAILBOX = "mailbox"
    SERVER = "server"
    MESSAGE = "message"
    ATTACHMENT = "attachment"
    PARSER = "parser"
    DATABASE = "database"
    UNEXPECTED = "unexpected"

    @property
    def label(self) -> str:
        """Kurzbezeichnung für Zusammenfassung und Status."""
        return _LABELS[self]

    @property
    def action(self) -> str:
        """Was der Benutzer tun kann."""
        return _ACTIONS[self]


_LABELS = {
    FetchErrorKind.CONFIGURATION: "Postfach nicht vollständig eingerichtet",
    FetchErrorKind.CREDENTIALS: "Kein Passwort für das Postfach verfügbar",
    FetchErrorKind.UNSUPPORTED_AUTH: "Anmeldeverfahren wird noch nicht unterstützt",
    FetchErrorKind.CONNECTION: "IMAP-Verbindung fehlgeschlagen",
    FetchErrorKind.AUTHENTICATION: "Anmeldung am Mailserver fehlgeschlagen",
    FetchErrorKind.TLS: "Sichere Verbindung (TLS) fehlgeschlagen",
    FetchErrorKind.TIMEOUT: "Zeitüberschreitung: Der Mailserver antwortet nicht",
    FetchErrorKind.MAILBOX: "Postfachordner nicht erreichbar",
    FetchErrorKind.SERVER: "Der Mailserver meldet einen Fehler",
    FetchErrorKind.MESSAGE: "Nachricht konnte nicht gelesen werden",
    FetchErrorKind.ATTACHMENT: "Anhang konnte nicht verarbeitet werden",
    FetchErrorKind.PARSER: "Bestellung konnte nicht ausgewertet werden",
    FetchErrorKind.DATABASE: "Datenbankfehler",
    FetchErrorKind.UNEXPECTED: "Unerwarteter Fehler beim Abruf",
}
_ACTIONS = {
    FetchErrorKind.CONFIGURATION: "Server, Benutzer und Ordner unter Einstellungen → "
    "Postfächer eintragen und das Postfach dem Firmenprofil zuordnen.",
    FetchErrorKind.CREDENTIALS: "Passwort unter Einstellungen → Postfächer speichern.",
    FetchErrorKind.UNSUPPORTED_AUTH: "OAuth2 (etwa Microsoft 365) folgt in einer späteren Version. "
    "Bis dahin ein Postfach mit Passwortanmeldung verwenden.",
    FetchErrorKind.CONNECTION: "Netzwerk und Servername prüfen und später erneut abrufen.",
    FetchErrorKind.AUTHENTICATION: "Benutzername und Passwort unter Einstellungen → "
    "Postfächer prüfen.",
    FetchErrorKind.TLS: "Servername und Verschlüsselung prüfen. Die Zertifikatsprüfung "
    "bleibt aktiv.",
    FetchErrorKind.TIMEOUT: "Netzwerkverbindung prüfen und später erneut abrufen.",
    FetchErrorKind.MAILBOX: "Ordnernamen unter Einstellungen → Postfächer prüfen.",
    FetchErrorKind.SERVER: "Später erneut abrufen; bei Wiederholung den Support kontaktieren.",
    FetchErrorKind.MESSAGE: "Die Mail bleibt unverändert im Postfach; Details im Protokoll.",
    FetchErrorKind.ATTACHMENT: "Der Auftrag wurde trotzdem angelegt; Anhang im Mailprogramm "
    "prüfen.",
    FetchErrorKind.PARSER: "Die Mail ist gespeichert; bitte den Support kontaktieren.",
    FetchErrorKind.DATABASE: "Bereits abgerufene Aufträge sind gespeichert. Programm neu starten; "
    "bei Wiederholung den Support kontaktieren.",
    FetchErrorKind.UNEXPECTED: "Bereits abgerufene Aufträge sind gespeichert; Details im "
    "Protokoll.",
}
IMAP_KINDS = {
    ImapErrorKind.TLS: FetchErrorKind.TLS,
    ImapErrorKind.AUTHENTICATION: FetchErrorKind.AUTHENTICATION,
    ImapErrorKind.NETWORK: FetchErrorKind.CONNECTION,
    ImapErrorKind.TIMEOUT: FetchErrorKind.TIMEOUT,
    ImapErrorKind.MAILBOX: FetchErrorKind.MAILBOX,
    ImapErrorKind.PROTOCOL: FetchErrorKind.SERVER,
    ImapErrorKind.OVERSIZED: FetchErrorKind.MESSAGE,
}


class FetchStage(StrEnum):
    """Abschnitt des Abrufs (für Fortschrittsanzeige)."""

    CONNECTING = "connecting"
    LISTING = "listing"
    LOADING = "loading"
    RETRYING = "retrying"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class FetchProgress:
    """Fortschritt ohne Mailinhalte."""

    stage: FetchStage
    current: int
    total: int
    text: str


@dataclass(frozen=True, slots=True)
class MailProblem:
    """Fehler einer einzelnen Mail; nur technische Kennungen, nie Inhalte."""

    kind: FetchErrorKind
    account_id: str
    uid: int | None
    code: str


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """Globaler Fehler, der den Abruf beendet hat."""

    kind: FetchErrorKind
    account_id: str

    @property
    def what(self) -> str:
        """Was passiert ist."""
        return self.kind.label

    @property
    def action(self) -> str:
        """Was zu tun ist."""
        return self.kind.action


@dataclass(slots=True)
class FetchSummary:
    """Ergebnis eines Abrufs; alle Zähler beziehen sich auf diesen Lauf."""

    accounts: list[str] = field(default_factory=list)
    checked: int = 0
    orders: int = 0
    review: int = 0
    possible_duplicates: int = 0
    known: int = 0
    sender_filtered: int = 0
    spam: int = 0
    auto_replies: int = 0
    oversized: int = 0
    failed: int = 0
    deferred: int = 0
    problems: list[MailProblem] = field(default_factory=list)
    new_order_ids: list[str] = field(default_factory=list)
    cancelled: bool = False
    failure: FetchFailure | None = None
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        """Vollständig durchgelaufen."""
        return self.failure is None and not self.cancelled

    def lines(self) -> list[str]:
        """Zeilen der Zusammenfassung; leere Kategorien entfallen."""
        lines = [f"{self.checked} {'Nachricht' if self.checked == 1 else 'Nachrichten'} geprüft"]
        entries = (
            (self.orders, "{} {} erkannt", ("Bestellung", "Bestellungen")),
            (self.review, "davon {} zur Prüfung", None),
            (
                self.possible_duplicates,
                "davon {} mögliche {}",
                ("Doppelbestellung", "Doppelbestellungen"),
            ),
            (self.known, "{} bereits bekannt", None),
            (self.sender_filtered, "{} wegen Absenderfilter verworfen", None),
            (self.spam, "{} als Spam markiert", None),
            (self.auto_replies, "{} automatische {}", ("Antwort", "Antworten")),
            (self.oversized, "{} wegen Größenlimit übersprungen", None),
            (self.failed, "{} nicht lesbar (Details im Protokoll)", None),
            (self.deferred, "{} weitere beim nächsten Abruf", None),
        )
        for count, template, words in entries:
            if count:
                word = (words[0] if count == 1 else words[1]) if words else ""
                lines.append(template.format(count, word) if words else template.format(count))
        return lines


class FetchCancelled(Exception):
    """Der Benutzer hat den Abruf abgebrochen."""


class CancelToken:
    """Thread-sicheres Abbruchsignal; ``wait`` ersetzt ``sleep`` bei Wiederholungen."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Abbruch anfordern."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Wurde der Abbruch angefordert?"""
        return self._event.is_set()

    def check(self) -> None:
        """Wirft ``FetchCancelled`` nach angefordertem Abbruch."""
        if self._event.is_set():
            raise FetchCancelled

    def sleep(self, seconds: float) -> None:
        """Wartet, endet aber sofort bei Abbruch."""
        if self._event.wait(seconds):
            raise FetchCancelled


@dataclass(frozen=True, slots=True)
class FetchTarget:
    """Ein Postfach und das Firmenprofil, dem seine Aufträge gehören."""

    profile: ImportProfile
    account: MailAccount


@dataclass(frozen=True, slots=True)
class SourceHooks:
    """Vom Dienst an die Quelle: abbrechbares Warten, Meldung von Wiederholungen.

    ``retries=False`` (Verbindungstest): genau ein Versuch, damit ein nicht erreichbarer
    Server schnell gemeldet wird statt nach mehreren Minuten.
    """

    sleep: Callable[[float], None]
    on_retry: Callable[[int, ImapErrorKind, float], None]
    retries: bool = True


@dataclass(frozen=True, slots=True)
class _Incoming:
    """Eine Mail im Abruf: Ziel, geöffneter Ordner, Kopfdaten."""

    target: FetchTarget
    info: MailboxInfo
    envelope: Envelope


SourceFactory = Callable[[MailAccount, SourceHooks], MailSource]
ProgressCallback = Callable[[FetchProgress], None]
CatalogLookup = Callable[[str], tuple[int | None, Catalog]]


def fetch_target(settings: Settings) -> FetchTarget | FetchFailure:
    """Postfach des aktiven Profils oder der Grund, warum nicht abgerufen werden kann."""
    profile = settings.profile()
    try:
        account = settings.account(profile.mail_account_id)
    except KeyError:
        return FetchFailure(FetchErrorKind.CONFIGURATION, profile.mail_account_id)
    if not account.enabled or not account.host or not account.username:
        return FetchFailure(FetchErrorKind.CONFIGURATION, account.id)
    if account.auth_method is AuthMethod.OAUTH2:
        return FetchFailure(FetchErrorKind.UNSUPPORTED_AUTH, account.id)
    return FetchTarget(profile, account)


def select_body(mail: ParsedMail) -> str:
    """Text für die Erkennung: der Textteil, außer er ist nur ein Platzhalter für HTML."""
    plain, html = mail.text.strip(), mail.html_text.strip()
    if not plain or (len(plain) < PLACEHOLDER_TEXT_CHARS and len(html) > 3 * len(plain)):
        return html or plain
    return plain


def _sender_allowed(sender: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    domain = sender.rpartition("@")[2]
    for pattern in patterns:
        wanted = pattern.strip().lower()
        if wanted.startswith("@"):
            if domain == wanted[1:] or domain.endswith("." + wanted[1:]):
                return True
        elif sender == wanted:
            return True
    return False


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class _DatabaseFailure(Exception):
    """Speichern unmöglich; beendet den Abruf."""


class MailFetchService:
    """Ruft Postfächer ab und legt Aufträge an. Läuft in einem eigenen Thread mit eigener
    Datenbankverbindung; die Oberfläche spricht nie direkt mit IMAP."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        sources: SourceFactory,
        catalogs: CatalogLookup,
        *,
        limits: MailLimits = MAIL,
        ids: Callable[[str], str] = _new_id,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._sources = sources
        self._catalogs = catalogs
        self._limits = limits
        self._ids = ids
        self._active: MailSource | None = None

    def abort(self) -> None:
        """Aus einem anderen Thread: laufende Netzwerkoperation abbrechen."""
        source = self._active
        if source is not None:
            source.abort()

    def run(
        self,
        targets: Sequence[FetchTarget],
        token: CancelToken,
        progress: ProgressCallback = lambda _p: None,
    ) -> FetchSummary:
        """Ruft alle Ziele nacheinander ab; ein globaler Fehler beendet den Lauf sauber."""
        summary = FetchSummary(accounts=[t.account.name for t in targets])
        _log.info("Abruf gestartet: %d Postfach/Postfächer", len(targets))
        try:
            for target in targets:
                token.check()
                self._run_target(target, token, summary, progress)
                if summary.failure is not None:
                    break
        except FetchCancelled:
            summary.cancelled = True
            _log.info("Abruf abgebrochen")
        except _DatabaseFailure:
            summary.failure = FetchFailure(FetchErrorKind.DATABASE, "")
        summary.finished_at = self._clock.now()
        progress(FetchProgress(FetchStage.DONE, 1, 1, "Abruf beendet"))
        _log.info(
            "Abruf beendet: %d geprüft, %d Aufträge, %d bekannt, %d Filter, %d Spam, "
            "%d automatisch, %d zu groß, %d Fehler, Status %s",
            summary.checked,
            summary.orders,
            summary.known,
            summary.sender_filtered,
            summary.spam,
            summary.auto_replies,
            summary.oversized,
            summary.failed,
            "abgebrochen"
            if summary.cancelled
            else (summary.failure.kind if summary.failure else "ok"),
        )
        return summary

    def _hooks(self, token: CancelToken, progress: ProgressCallback) -> SourceHooks:
        def on_retry(attempt: int, kind: ImapErrorKind, delay: float) -> None:
            _log.warning("Abruf: Verbindung gestört (%s), Versuch %d", kind.value, attempt + 1)
            text = f"Verbindung unterbrochen, neuer Versuch in {delay:.0f} s"
            progress(FetchProgress(FetchStage.RETRYING, attempt, 0, text))

        return SourceHooks(sleep=token.sleep, on_retry=on_retry)

    def _run_target(
        self,
        target: FetchTarget,
        token: CancelToken,
        summary: FetchSummary,
        progress: ProgressCallback,
    ) -> None:
        account = target.account
        try:
            source = self._sources(account, self._hooks(token, progress))
        except CredentialError:
            _log.error("Abruf %s: kein Passwort verfügbar", account.id)
            summary.failure = FetchFailure(FetchErrorKind.CREDENTIALS, account.id)
            return
        self._active = source
        try:
            progress(FetchProgress(FetchStage.CONNECTING, 0, 0, f"Verbinde mit „{account.name}“"))
            info = source.open()
            token.check()
            progress(FetchProgress(FetchStage.LISTING, 0, 0, "Nachrichten werden aufgelistet"))
            uids = source.uids()
            known = (
                MailRepository(self._conn).known_uids(account.id, source.folder, info.uidvalidity)
                if info.uidvalidity is not None
                else set()
            )
            fresh = [uid for uid in uids if uid not in known]
            batch, rest = (
                fresh[: account.max_messages_per_run],
                fresh[account.max_messages_per_run :],
            )
            summary.known += len(uids) - len(fresh)
            summary.deferred += len(rest)
            summary.checked += len(uids) - len(rest)
            _log.info(
                "Abruf %s: %d Nachrichten im Ordner, %d neu, %d in diesem Lauf",
                account.id,
                len(uids),
                len(fresh),
                len(batch),
            )
            token.check()
            envelopes = {e.uid: e for e in source.envelopes(batch)}
            vanished = len(batch) - len(envelopes)
            summary.checked -= vanished
            for position, uid in enumerate(batch, start=1):
                envelope = envelopes.get(uid)
                if envelope is None:
                    continue
                token.check()
                progress(
                    FetchProgress(
                        FetchStage.LOADING,
                        position,
                        len(batch),
                        f"Nachricht {position} von {len(batch)}",
                    )
                )
                self._one(source, _Incoming(target, info, envelope), summary, token)
        except ImapError as exc:
            if token.cancelled:
                raise FetchCancelled from None
            kind = IMAP_KINDS.get(exc.kind, FetchErrorKind.CONNECTION)
            _log.error("Abruf %s abgebrochen: %s", account.id, kind.value)
            summary.failure = FetchFailure(kind, account.id)
        except CredentialError:
            summary.failure = FetchFailure(FetchErrorKind.CREDENTIALS, account.id)
        finally:
            self._active = None
            source.close()

    def _one(
        self, source: MailSource, mail: _Incoming, summary: FetchSummary, token: CancelToken
    ) -> None:
        account, envelope = mail.target.account, mail.envelope
        if envelope.size > self._limits.max_message_bytes:
            summary.oversized += 1
            _log.info("Abruf %s: UID %d über dem Größenlimit", account.id, envelope.uid)
            return
        screening = screen(envelope)
        if screening.verdict is not Verdict.ACCEPT:
            if screening.verdict is Verdict.SPAM:
                summary.spam += 1
            else:
                summary.auto_replies += 1
            _log.info(
                "Abruf %s: UID %d übersprungen (%s)", account.id, envelope.uid, screening.reason
            )
            return
        sender = sender_address(read_headers(envelope.header))
        profile = mail.target.profile
        action = evaluate_sender(profile, sender).action if sender else profile.sender_default
        if not _sender_allowed(sender, account.allowed_senders) or action is SenderAction.IGNORE:
            summary.sender_filtered += 1
            _log.info("Abruf %s: UID %d durch Absenderfilter verworfen", account.id, envelope.uid)
            return
        try:
            raw = source.fetch(envelope.uid, envelope.size, self._limits.max_message_bytes)
        except ImapError as exc:
            if exc.kind is ImapErrorKind.OVERSIZED:
                summary.oversized += 1
                return
            if exc.kind is not ImapErrorKind.PROTOCOL:
                raise
            self._problem(summary, FetchErrorKind.MESSAGE, mail, "unreadable")
            summary.failed += 1
            return
        token.check()
        self._intake(raw, mail, action, summary)

    @staticmethod
    def _problem(summary: FetchSummary, kind: FetchErrorKind, mail: _Incoming, code: str) -> None:
        account_id, uid = mail.target.account.id, mail.envelope.uid
        summary.problems.append(MailProblem(kind, account_id, uid, code))
        _log.warning("Abruf %s: UID %s – %s (%s)", account_id, uid, kind.value, code)

    @staticmethod
    def _metadata(
        mail_id: str, mail: _Incoming, parsed: ParsedMail | None, raw: bytes, content_hash: str
    ) -> MailMetadata:
        return MailMetadata(
            id=mail_id,
            account_id=mail.target.account.id,
            folder=mail.info.folder,
            uidvalidity=mail.info.uidvalidity,
            uid=mail.envelope.uid,
            message_id=parsed.message_id if parsed else "",
            content_hash=content_hash,
            raw_sha256=parsed.raw_sha256 if parsed else content_hash,
            sender=parsed.sender if parsed else "",
            sender_name=parsed.sender_name if parsed else "",
            subject=parsed.subject if parsed else "",
            date_header=parsed.date if parsed else None,
            size=len(raw),
        )

    def _rejected(
        self, raw: bytes, mail: _Incoming, rejected: MailRejected, summary: FetchSummary
    ) -> None:
        """Unlesbare Mail: mit Rohdaten gespeichert (Support), nie erneut verarbeitet."""
        if rejected.code == "MAIL_TOO_LARGE":
            summary.oversized += 1
            return
        digest = hashlib.sha256(raw).hexdigest()
        meta = self._metadata(self._ids("mail"), mail, None, raw, digest)
        if self._persist(meta, MailState.FAILED, raw, summary, error=rejected.code):
            self._problem(summary, FetchErrorKind.MESSAGE, mail, rejected.code)
            summary.failed += 1

    def _intake(
        self, raw: bytes, mail: _Incoming, action: SenderAction, summary: FetchSummary
    ) -> None:
        try:
            parsed = parse_mail(raw, self._limits)
        except MailRejected as rejected:
            self._rejected(raw, mail, rejected, summary)
            return
        account = mail.target.account
        body = select_body(parsed)
        identity = MailIdentity(
            account.id,
            mail.info.folder,
            mail.info.uidvalidity,
            mail.envelope.uid,
            parsed.message_id,
            content_fingerprint(parsed.sender, parsed.subject, body),
        )
        meta = self._metadata(self._ids("mail"), mail, parsed, raw, identity.content_hash)
        check = check_mail(MailRepository(self._conn), identity)
        if check.kind is MailDuplicate.SAME_MAIL:
            summary.known += 1
            return
        if check.kind is MailDuplicate.SAME_MESSAGE_ID:
            duplicate = check.existing_mail_id
            if self._persist(meta, MailState.DUPLICATE, None, summary, duplicate_of=duplicate):
                summary.known += 1
            return
        blocked = sum(
            1 for a in parsed.attachments if a.report.verdict is AttachmentVerdict.BLOCKED
        )
        if blocked:
            self._problem(summary, FetchErrorKind.ATTACHMENT, mail, f"{blocked} gesperrt")
        duplicate_of = check.existing_mail_id if check.kind is MailDuplicate.SAME_CONTENT else None
        try:
            order = self._order(
                parsed,
                body,
                profile=mail.target.profile,
                mail_id=meta.id,
                duplicate_of=duplicate_of,
                action=action,
            )
        except Exception as exc:
            code = f"PARSER: {type(exc).__name__}"
            if self._persist(meta, MailState.FAILED, raw, summary, error=code):
                self._problem(summary, FetchErrorKind.PARSER, mail, code)
                summary.failed += 1
            return
        stored = self._persist(
            meta,
            MailState.EXTRACTED,
            raw,
            summary,
            order=order,
            keys=mail_keys(identity),
            duplicate_of=duplicate_of,
        )
        if stored:
            summary.orders += 1
            summary.new_order_ids.append(order.id)
            summary.review += order.status is OrderStatus.NEEDS_REVIEW
            summary.possible_duplicates += duplicate_of is not None

    def _order(
        self,
        mail: ParsedMail,
        body: str,
        *,
        profile: ImportProfile,
        mail_id: str,
        duplicate_of: str | None,
        action: SenderAction,
    ) -> Order:
        version, catalog = self._catalogs(profile.id)
        has_catalog = version is not None
        engine = ExtractionEngine(catalog.knows_name if has_catalog else None)
        mail_date = mail.date.date() if mail.date else None
        context = ParseContext(
            normalize(body, mail.subject),
            mail.subject,
            mail.sender,
            mail.sender_name,
            mail_date,
            (profile.supplier,),
        )
        result = engine.extract(context)
        order = build_order(
            self._ids("auftrag"),
            mail_id,
            result,
            sender_email=mail.sender,
            profile=profile,
            matcher=ArticleMatcher(catalog) if has_catalog else None,
        )
        order = replace(order, profile_id=profile.id)
        today = self._clock.now().date()
        findings = review_order(self._conn, order, profile, today, duplicate_of=duplicate_of)
        status = status_after_validation(findings, order.acknowledged)
        if action is SenderAction.REVIEW:
            status = OrderStatus.NEEDS_REVIEW
        return replace(order, status=status)

    def _persist(
        self,
        meta: MailMetadata,
        state: MailState,
        raw: bytes | None,
        summary: FetchSummary,
        *,
        error: str | None = None,
        duplicate_of: str | None = None,
        order: Order | None = None,
        keys: tuple[str, ...] = (),
    ) -> bool:
        """Speichert in einer Transaktion; ``False``, wenn die Mail inzwischen bekannt ist."""
        now = self._clock.now()
        try:
            with transaction(self._conn):
                mails = MailRepository(self._conn)
                mails.insert(meta, state, now, raw=raw, duplicate_of=duplicate_of)
                if error is not None:
                    mails.set_state(meta.id, state, now, error)
                if order is not None:
                    OrderRepository(self._conn).insert(order, "Aus Mail übernommen", now)
                keys_repo = IdempotencyRepository(self._conn)
                for key in keys:
                    keys_repo.register(
                        key, "mail", now, mail_id=meta.id, order_id=order.id if order else None
                    )
                JournalRepository(self._conn).append(
                    "mail",
                    meta.id,
                    "mail_fetched",
                    now,
                    {
                        "state": state.value,
                        "account": meta.account_id,
                        "uid": meta.uid,
                        "order": order.id if order else None,
                    },
                )
        except sqlite3.IntegrityError as exc:
            if meta.uid is not None and meta.uidvalidity is not None:
                existing = MailRepository(self._conn).find_by_uid(
                    meta.account_id, meta.folder, meta.uidvalidity, meta.uid
                )
                if existing:
                    summary.known += 1
                    return False
            _log.error("Abruf: Datenbank lehnt Speichern ab (%s)", type(exc).__name__)
            raise _DatabaseFailure from exc
        except (sqlite3.Error, AppError) as exc:
            _log.error("Abruf: Datenbankfehler (%s)", type(exc).__name__)
            raise _DatabaseFailure from exc
        return True
