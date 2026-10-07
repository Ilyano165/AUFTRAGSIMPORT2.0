"""Aufträge abrufen aus Sicht der Oberfläche: Auftrag für den Hintergrund-Thread.

``FetchJob`` enthält alles, was ein Abruf braucht, als unveränderliche Daten. Er öffnet im
Hintergrund-Thread eine eigene Datenbankverbindung (SQLite-Verbindungen gehören einem
Thread) und führt ``MailFetchService`` aus. Die Oberfläche berührt weder IMAP noch die
Verbindung dieses Threads.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..catalog.backup import CatalogBackups
from ..catalog.service import CatalogManager
from ..config.schema import CredentialSource, MailAccount
from ..domain.errors import AppError, CredentialError, UserMessage
from ..domain.ports import Clock
from ..infrastructure.db import Database
from ..infrastructure.logging_setup import get_logger
from ..ingest.imap import MAX_ATTEMPTS, ImapError
from ..ingest.sources import FolderMailSource, ImapMailSource, MailSource
from ..security.credentials import CredentialStore, resolve_secret
from ..services.mail_fetch import (
    IMAP_KINDS,
    CancelToken,
    FetchErrorKind,
    FetchFailure,
    FetchSummary,
    FetchTarget,
    MailFetchService,
    ProgressCallback,
    SourceFactory,
    SourceHooks,
)

_log = get_logger("abruf")


def missing_password(account: MailAccount) -> CredentialError:
    """Kein Passwort für das Konto gespeichert."""
    return CredentialError(
        "CREDENTIAL_MISSING",
        UserMessage(
            what=f"Für das Postfach „{account.name}“ ist kein Passwort gespeichert",
            why="Die Windows-Anmeldeinformationsverwaltung enthält keinen Eintrag",
            unchanged="Es wurde nichts abgerufen",
            action="Passwort unter Einstellungen → Postfächer speichern",
        ),
    )


def imap_sources(store: CredentialStore) -> SourceFactory:
    """IMAP-Quellen; das Passwort kommt aus dem Anmeldespeicher, nie aus einer Datei."""

    def make(account: MailAccount, hooks: SourceHooks) -> MailSource:
        secret = resolve_secret(
            store,
            account.id,
            allow_environment=account.credential_source is CredentialSource.ENVIRONMENT,
        )
        if not secret.value:
            raise missing_password(account)
        value = secret.value
        return ImapMailSource(
            account,
            lambda: value,
            sleep=hooks.sleep,
            on_retry=hooks.on_retry,
            max_attempts=MAX_ATTEMPTS if hooks.retries else 1,
        )

    return make


def folder_sources(base: Path, *, shared: bool = False) -> SourceFactory:
    """Testpostfach: ein Ordner je Konto unter ``base`` oder (``shared``) ein Ordner für alle."""

    def make(account: MailAccount, _hooks: SourceHooks) -> MailSource:
        return FolderMailSource(account.id, base if shared else base / account.id)

    return make


@dataclass(frozen=True, slots=True)
class FetchJob:
    """Ein Abruf als Daten; ``run`` läuft im Hintergrund-Thread."""

    database_path: Path
    data_dir: Path
    clock: Clock
    sources: SourceFactory
    targets: tuple[FetchTarget, ...]

    def run(
        self,
        token: CancelToken,
        progress: ProgressCallback,
        register: Callable[[MailFetchService], None] = lambda _s: None,
    ) -> FetchSummary:
        """Eigene Verbindung, Dienst ausführen, Verbindung schließen."""
        try:
            conn = Database(self.database_path).connect()
        except (AppError, sqlite3.Error):
            _log.error("Abruf: Datenbank nicht erreichbar")
            return FetchSummary(failure=FetchFailure(FetchErrorKind.DATABASE, ""))
        try:
            catalogs = CatalogManager(
                conn, self.clock, CatalogBackups(self.data_dir / "katalog-backups")
            )
            service = MailFetchService(conn, self.clock, self.sources, catalogs.catalog)
            register(service)
            return service.run(self.targets, token, progress)
        finally:
            conn.close()


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Ergebnis eines Verbindungstests."""

    messages: int = 0
    folder: str = ""
    failure: FetchFailure | None = None


def probe(account: MailAccount, source: Callable[[SourceHooks], MailSource]) -> ProbeResult:
    """Verbindung testen: anmelden, Ordner lesend öffnen, Nachrichten zählen. Lädt nichts."""
    token = CancelToken()
    hooks = SourceHooks(sleep=token.sleep, on_retry=lambda *_args: None, retries=False)
    try:
        mail_source = source(hooks)
    except CredentialError:
        return ProbeResult(failure=FetchFailure(FetchErrorKind.CREDENTIALS, account.id))
    try:
        info = mail_source.open()
        return ProbeResult(len(mail_source.uids()), info.folder)
    except ImapError as exc:
        kind = IMAP_KINDS.get(exc.kind, FetchErrorKind.CONNECTION)
        return ProbeResult(failure=FetchFailure(kind, account.id))
    finally:
        mail_source.close()
