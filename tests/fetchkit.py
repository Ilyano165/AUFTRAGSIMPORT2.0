"""Testhilfen für den Mail-Abruf: Attrappen-Quelle, Mailbaukasten, Dienst-Gerüst."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from icware_auftragsimport.app.demo import build_demo
from icware_auftragsimport.branding import LOGGER_NAME
from icware_auftragsimport.catalog.backup import CatalogBackups
from icware_auftragsimport.catalog.service import CatalogManager
from icware_auftragsimport.config.schema import ImportProfile, MailAccount, Settings
from icware_auftragsimport.domain.errors import UserMessage
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.ingest.imap import Envelope, ImapError, ImapErrorKind, MailboxInfo
from icware_auftragsimport.security.limits import MAIL, MailLimits
from icware_auftragsimport.services.mail_fetch import (
    CancelToken,
    FetchProgress,
    FetchSummary,
    FetchTarget,
    MailFetchService,
    SourceFactory,
    SourceHooks,
)

NOW = datetime(2026, 10, 2, 11, 0, tzinfo=ZoneInfo("Europe/Berlin"))
SENDER = "Martin Vogt <m.vogt@gasthaus.example>"
ORDER = """Guten Morgen,

bitte liefern Sie:

6 x MW-0710 Mineralwasser still 12 × 0,7 l
4 x AS-1000 Apfelschorle 12 × 1,0 l

Unsere Bestellnummer: {reference}
Zahlung auf Rechnung.

Viele Grüße
Martin Vogt
Gasthaus Lindenhof
Lindenstraße 12
53783 Eitorf
"""


def order_text(reference: str = "GH-1") -> str:
    """Bestelltext mit zwei Katalogartikeln."""
    return ORDER.format(reference=reference)


def build_mail(
    body: str | None = None,
    *,
    sender: str = SENDER,
    subject: str = "Bestellung",
    message_id: str = "<m1@gasthaus.example>",
    headers: dict[str, str] | None = None,
    html: bool = False,
    attachment: tuple[str, bytes, str] | None = None,
) -> bytes:
    """Wohlgeformte Mail als Bytes."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "bestellung@muster-getraenke.example"
    message["Subject"] = subject
    message["Date"] = format_datetime(NOW)
    message["Message-ID"] = message_id
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(
        body if body is not None else order_text(), subtype="html" if html else "plain"
    )
    if attachment is not None:
        name, data, mime = attachment
        main, sub = mime.split("/", 1)
        message.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return message.as_bytes()


def imap_error(kind: ImapErrorKind, what: str = "Fehler") -> ImapError:
    """ImapError wie vom Client erzeugt."""
    return ImapError(kind, UserMessage(what=what, why="Test", unchanged="-", action="-"))


@dataclass
class FakeMessage:
    """Nachricht im Attrappen-Postfach."""

    raw: bytes
    flags: frozenset[str] = frozenset()
    size: int | None = None


@dataclass
class FakeSource:
    """Postfach im Speicher; protokolliert Downloads, Threads und Abbrüche."""

    messages: dict[int, FakeMessage]
    account_id: str = "bestellungen"
    folder: str = "INBOX"
    uidvalidity: int | None = 7
    fail: dict[str, ImapError] = field(default_factory=dict)
    fail_fetch: dict[int, ImapError] = field(default_factory=dict)
    on_fetch: Callable[[int], None] | None = None
    fetched: list[int] = field(default_factory=list)
    threads: list[int] = field(default_factory=list)
    aborted: bool = False
    closed: int = 0

    def _maybe_fail(self, stage: str) -> None:
        if stage in self.fail:
            raise self.fail[stage]

    def open(self) -> MailboxInfo:
        self.threads.append(threading.get_ident())
        self._maybe_fail("open")
        return MailboxInfo(self.folder, self.uidvalidity, len(self.messages))

    def uids(self) -> list[int]:
        self._maybe_fail("uids")
        return sorted(self.messages)

    def envelopes(self, uids: Sequence[int]) -> list[Envelope]:
        self._maybe_fail("envelopes")
        found = []
        for uid in uids:
            message = self.messages[uid]
            head = message.raw.split(b"\n\n", 1)[0]
            size = message.size if message.size is not None else len(message.raw)
            found.append(Envelope(uid, size, message.flags, head))
        return found

    def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
        self.fetched.append(uid)
        self.threads.append(threading.get_ident())
        if self.on_fetch is not None:
            self.on_fetch(uid)
        if uid in self.fail_fetch:
            raise self.fail_fetch[uid]
        raw = self.messages[uid].raw
        if len(raw) > max_bytes:
            raise imap_error(ImapErrorKind.OVERSIZED)
        return raw

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed += 1


class Harness:
    """Echter Demobestand (Datei-Datenbank) und Abrufdienst."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        database, self.settings = build_demo(directory, NOW)
        self.database = database
        self.conn = database.connect()
        self.clock = FixedClock(NOW)
        self.catalogs = CatalogManager(
            self.conn, self.clock, CatalogBackups(directory / "katalog-backups")
        )

    @property
    def profile(self) -> ImportProfile:
        return self.settings.profile("standard")

    @property
    def account(self) -> MailAccount:
        return self.settings.account("bestellungen")

    def target(self, **changes: object) -> FetchTarget:
        from dataclasses import replace  # noqa: PLC0415

        profile = replace(self.profile, **{k: v for k, v in changes.items() if k != "account"})
        account = changes.get("account", self.account)
        assert isinstance(account, MailAccount)
        return FetchTarget(profile, account)

    def service(self, sources: SourceFactory, limits: MailLimits = MAIL) -> MailFetchService:
        return MailFetchService(
            self.conn, self.clock, sources, self.catalogs.catalog, limits=limits
        )

    def run(
        self,
        source: FakeSource,
        *,
        target: FetchTarget | None = None,
        limits: MailLimits = MAIL,
        token: CancelToken | None = None,
        progress: Callable[[FetchProgress], None] | None = None,
    ) -> FetchSummary:
        def factory(_account: MailAccount, _hooks: SourceHooks) -> FakeSource:
            return source

        service = self.service(factory, limits)
        return service.run(
            [target or self.target()], token or CancelToken(), progress or (lambda _p: None)
        )

    def count(self, table: str, where: str = "1", *args: object) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()
        return int(row[0])

    def settings_with(self, **changes: object) -> Settings:
        from dataclasses import replace  # noqa: PLC0415

        return replace(self.settings, **changes)  # type: ignore[arg-type]

    def mail_states(self) -> list[tuple[int, str]]:
        rows = self.conn.execute(
            "SELECT uid, state FROM mails WHERE uidvalidity != 1 ORDER BY uid"
        ).fetchall()
        return [(int(r["uid"]), str(r["state"])) for r in rows]


def messages(*raws: bytes) -> dict[int, FakeMessage]:
    """UIDs 1, 2, 3 … in Reihenfolge."""
    return {i: FakeMessage(raw) for i, raw in enumerate(raws, start=1)}


@contextmanager
def captured_logs() -> Iterator[list[str]]:
    """Alle Protokollzeilen der Anwendung, unabhängig von der Logging-Konfiguration."""
    lines: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    logger = logging.getLogger(LOGGER_NAME)
    handler = _Collect(logging.DEBUG)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def database_error() -> sqlite3.OperationalError:
    """Simulierter Schreibfehler (etwa volle Platte)."""
    return sqlite3.OperationalError("disk I/O error")
