"""SQLite-Datenbank: Schema, Migrationen und Transaktionen.

Die Datenbank ist die einzige Wahrheit über Mails, Aufträge und Exporte. Jeder
Zustandswechsel wird hier gespeichert, bevor er nach außen wirkt.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..domain.errors import StoreError, UserMessage
from .logging_setup import get_logger

BUSY_TIMEOUT_SECONDS = 10.0
_log = get_logger("db")

_SCHEMA_V1 = """
CREATE TABLE mails (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity INTEGER,
    uid INTEGER,
    message_id TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    date_header TEXT,
    size INTEGER NOT NULL,
    state TEXT NOT NULL,
    error TEXT,
    duplicate_of TEXT REFERENCES mails(id),
    raw BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX mails_by_uid ON mails(account_id, folder, uidvalidity, uid)
    WHERE uid IS NOT NULL AND uidvalidity IS NOT NULL;
CREATE INDEX mails_by_message_id ON mails(message_id);
CREATE INDEX mails_by_content ON mails(content_hash);

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    mail_id TEXT REFERENCES mails(id),
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    document_number TEXT UNIQUE,
    customer_key TEXT NOT NULL DEFAULT '',
    customer_reference TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX orders_by_status ON orders(status);
CREATE INDEX orders_by_reference ON orders(customer_key, customer_reference);

CREATE TABLE order_revisions (
    order_id TEXT NOT NULL REFERENCES orders(id),
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (order_id, revision)
);

CREATE TABLE export_jobs (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('live', 'test', 'dry_run')),
    state TEXT NOT NULL,
    file_name TEXT,
    target_path TEXT,
    payload_sha256 TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX export_jobs_one_live_per_order ON export_jobs(order_id)
    WHERE mode = 'live' AND state NOT IN ('failed', 'cancelled');

CREATE TABLE journal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX journal_by_entity ON journal(entity, entity_id);

CREATE TABLE idempotency_keys (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    mail_id TEXT REFERENCES mails(id),
    order_id TEXT REFERENCES orders(id),
    created_at TEXT NOT NULL
);

CREATE TABLE counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE catalog_versions (
    version INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    article_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX catalog_one_active_per_profile ON catalog_versions(profile_id)
    WHERE active = 1;

CREATE TABLE catalog_articles (
    version INTEGER NOT NULL REFERENCES catalog_versions(version),
    number TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (version, number)
);
"""

_SCHEMA_V2 = """
ALTER TABLE export_jobs ADD COLUMN adapter_id TEXT NOT NULL DEFAULT '';
ALTER TABLE export_jobs ADD COLUMN adapter_version TEXT NOT NULL DEFAULT '';
ALTER TABLE export_jobs ADD COLUMN spec_version TEXT NOT NULL DEFAULT '';
ALTER TABLE export_jobs ADD COLUMN document_number TEXT NOT NULL DEFAULT '';
ALTER TABLE export_jobs ADD COLUMN order_reference TEXT NOT NULL DEFAULT '';
ALTER TABLE export_jobs ADD COLUMN result TEXT NOT NULL DEFAULT '';
ALTER TABLE export_jobs ADD COLUMN report TEXT NOT NULL DEFAULT '{}';
ALTER TABLE export_jobs ADD COLUMN finished_at TEXT;
CREATE INDEX export_jobs_by_state ON export_jobs(state)
"""

_SCHEMA_V3 = """
CREATE TABLE directory_pins (
    profile_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    path TEXT NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    pinned_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, purpose)
)
"""

_SCHEMA_V4 = """
ALTER TABLE orders ADD COLUMN profile_id TEXT NOT NULL DEFAULT '';
CREATE INDEX orders_profile_status ON orders(profile_id, status);
ALTER TABLE catalog_versions ADD COLUMN source_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE catalog_versions ADD COLUMN mapping TEXT NOT NULL DEFAULT '{}';
ALTER TABLE catalog_versions ADD COLUMN changes TEXT NOT NULL DEFAULT '{}';
ALTER TABLE catalog_versions ADD COLUMN backup_file TEXT NOT NULL DEFAULT '';
ALTER TABLE catalog_versions ADD COLUMN based_on INTEGER;
ALTER TABLE catalog_versions ADD COLUMN note TEXT NOT NULL DEFAULT '';
ALTER TABLE catalog_versions ADD COLUMN number INTEGER NOT NULL DEFAULT 0;
"""

MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4)


def _store_error(what: str, why: str, action: str) -> StoreError:
    return StoreError(
        "STORE_UNAVAILABLE",
        UserMessage(
            what=what,
            why=why,
            unchanged="Es wurden keine Aufträge verändert oder exportiert",
            action=action,
        ),
    )


class Database:
    """Zugriff auf die lokale Datenbankdatei."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Öffnet eine Verbindung mit WAL, Fremdschlüsseln und vollständigem Sync."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        except sqlite3.DatabaseError as exc:
            raise _store_error(
                "Die Datenbank konnte nicht geöffnet werden",
                f"SQLite meldet: {exc}",
                "Bitte den Support mit einem Diagnosebericht kontaktieren",
            ) from exc
        return conn

    def migrate(self, conn: sqlite3.Connection) -> int:
        """Bringt das Schema auf den neuesten Stand und gibt die Version zurück."""
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        target = len(MIGRATIONS)
        if current > target:
            raise _store_error(
                "Die Datenbank stammt aus einer neueren Programmversion",
                f"Schema {current}, dieses Programm kennt bis {target}",
                "Bitte die aktuelle Version des Auftrags-Imports installieren",
            )
        if current == target:
            return current
        if current > 0:
            self._backup(conn, current)
        for version in range(current, target):
            with transaction(conn):
                for statement in _split(MIGRATIONS[version]):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {version + 1}")
            _log.info("Datenbankschema auf Version %d gebracht", version + 1)
        return target

    def _backup(self, conn: sqlite3.Connection, version: int) -> None:
        target = self.path.with_name(f"{self.path.stem}-vor-migration-v{version}{self.path.suffix}")
        backup = sqlite3.connect(target)
        try:
            conn.backup(backup)
        finally:
            backup.close()


def _split(script: str) -> list[str]:
    return [part.strip() for part in script.split(";") if part.strip()]


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Schreibtransaktion; bei jeder Ausnahme wird vollständig zurückgerollt."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
