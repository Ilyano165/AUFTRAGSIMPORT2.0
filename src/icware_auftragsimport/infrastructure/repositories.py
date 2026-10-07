"""Datenzugriff je Tabelle. Transaktionen steuert der aufrufende Dienst."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from ..domain.codec import decode_article, dumps, encode_article, order_from_dict, order_to_dict
from ..domain.errors import ConcurrencyError, StoreError, UserMessage
from ..domain.identity import normalize_reference
from ..domain.models import Article, MailMetadata, Order
from ..domain.status import ExportJobState, ExportMode, MailState, OrderStatus
from ..security.redaction import redact_mapping


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


def _damaged(entity: str, entity_id: str, exc: Exception) -> StoreError:
    return StoreError(
        "STORE_RECORD_DAMAGED",
        UserMessage(
            what=f"Der Datensatz {entity} {entity_id} ist beschädigt",
            why=f"{type(exc).__name__}: {exc}",
            unchanged="Der Datensatz wurde nicht verändert und nicht exportiert",
            action="Bitte einen Diagnosebericht erstellen und den Support kontaktieren",
        ),
    )


@dataclass(frozen=True, slots=True)
class MailRecord:
    """Gespeicherte Mail mit Verarbeitungsstand."""

    metadata: MailMetadata
    state: MailState
    error: str | None
    duplicate_of: str | None


class MailRepository:
    """Tabelle ``mails``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(
        self,
        meta: MailMetadata,
        state: MailState,
        now: datetime,
        *,
        raw: bytes | None = None,
        duplicate_of: str | None = None,
    ) -> None:
        """Legt eine Mail an; Eindeutigkeit über Konto, Ordner, UIDVALIDITY und UID."""
        self._conn.execute(
            "INSERT INTO mails (id, account_id, folder, uidvalidity, uid, message_id, "
            "content_hash, raw_sha256, sender, sender_name, subject, date_header, size, "
            "state, duplicate_of, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meta.id,
                meta.account_id,
                meta.folder,
                meta.uidvalidity,
                meta.uid,
                meta.message_id,
                meta.content_hash,
                meta.raw_sha256,
                meta.sender,
                meta.sender_name,
                meta.subject,
                meta.date_header.isoformat() if meta.date_header else None,
                meta.size,
                state.value,
                duplicate_of,
                raw,
                _iso(now),
                _iso(now),
            ),
        )

    def get(self, mail_id: str) -> MailRecord:
        """Liest eine Mail; ``KeyError``, wenn sie fehlt."""
        row = self._conn.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()
        if row is None:
            raise KeyError(mail_id)
        date_header = datetime.fromisoformat(row["date_header"]) if row["date_header"] else None
        meta = MailMetadata(
            id=row["id"],
            account_id=row["account_id"],
            folder=row["folder"],
            uidvalidity=row["uidvalidity"],
            uid=row["uid"],
            message_id=row["message_id"],
            content_hash=row["content_hash"],
            raw_sha256=row["raw_sha256"],
            sender=row["sender"],
            sender_name=row["sender_name"],
            subject=row["subject"],
            date_header=date_header,
            size=row["size"],
        )
        return MailRecord(meta, MailState(row["state"]), row["error"], row["duplicate_of"])

    def find_by_uid(self, account_id: str, folder: str, uidvalidity: int, uid: int) -> str | None:
        """Mail-ID zur Serveradresse oder ``None``."""
        row = self._conn.execute(
            "SELECT id FROM mails WHERE account_id = ? AND folder = ? AND uidvalidity = ? "
            "AND uid = ?",
            (account_id, folder, uidvalidity, uid),
        ).fetchone()
        return None if row is None else str(row["id"])

    def known_uids(self, account_id: str, folder: str, uidvalidity: int) -> set[int]:
        """Alle bereits gespeicherten UIDs eines Ordners (für den Abruf ohne erneuten Download)."""
        rows = self._conn.execute(
            "SELECT uid FROM mails WHERE account_id = ? AND folder = ? AND uidvalidity = ? "
            "AND uid IS NOT NULL",
            (account_id, folder, uidvalidity),
        ).fetchall()
        return {int(row["uid"]) for row in rows}

    def find_by_message_id(self, message_id: str) -> list[str]:
        """Mails mit gleicher Message-ID, älteste zuerst."""
        if not message_id:
            return []
        rows = self._conn.execute(
            "SELECT id FROM mails WHERE message_id = ? ORDER BY created_at, id", (message_id,)
        ).fetchall()
        return [str(row["id"]) for row in rows]

    def find_by_content_hash(self, content_hash: str) -> list[str]:
        """Mails mit gleichem Inhaltsfingerabdruck, älteste zuerst."""
        rows = self._conn.execute(
            "SELECT id FROM mails WHERE content_hash = ? ORDER BY created_at, id", (content_hash,)
        ).fetchall()
        return [str(row["id"]) for row in rows]

    def set_state(
        self, mail_id: str, state: MailState, now: datetime, error: str | None = None
    ) -> None:
        """Setzt den Verarbeitungsstand."""
        cursor = self._conn.execute(
            "UPDATE mails SET state = ?, error = ?, updated_at = ? WHERE id = ?",
            (state.value, error, _iso(now), mail_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(mail_id)

    def raw(self, mail_id: str) -> bytes | None:
        """Gespeicherte Rohmail, falls vorhanden."""
        row = self._conn.execute("SELECT raw FROM mails WHERE id = ?", (mail_id,)).fetchone()
        return None if row is None or row["raw"] is None else bytes(row["raw"])


@dataclass(frozen=True, slots=True)
class OrderRef:
    """Kurzinfo zu einem Auftrag für Duplikatprüfung und Listen."""

    id: str
    status: OrderStatus
    document_number: str | None
    revision: int


class OrderRepository:
    """Tabellen ``orders`` und ``order_revisions`` mit optimistischer Sperre."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, order: Order, reason: str, now: datetime) -> None:
        """Legt Revision 1 eines Auftrags an."""
        if order.revision != 1:
            raise ValueError("Neue Aufträge beginnen mit Revision 1")
        payload = dumps(order_to_dict(order))
        self._conn.execute(
            "INSERT INTO orders (id, mail_id, revision, status, document_number, customer_key, "
            "customer_reference, payload, created_at, updated_at, profile_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order.id,
                order.mail_id,
                order.revision,
                order.status.value,
                order.document_number,
                order.customer_key,
                normalize_reference(order.customer_reference.value or ""),
                payload,
                _iso(now),
                _iso(now),
                order.profile_id,
            ),
        )
        self._record_revision(order, payload, reason, now)

    def save(self, order: Order, reason: str, now: datetime) -> None:
        """Speichert eine neue Revision; die vorherige muss unverändert gespeichert sein."""
        payload = dumps(order_to_dict(order))
        cursor = self._conn.execute(
            "UPDATE orders SET revision = ?, status = ?, document_number = ?, customer_key = ?, "
            "customer_reference = ?, payload = ?, updated_at = ?, profile_id = ? "
            "WHERE id = ? AND revision = ?",
            (
                order.revision,
                order.status.value,
                order.document_number,
                order.customer_key,
                normalize_reference(order.customer_reference.value or ""),
                payload,
                _iso(now),
                order.profile_id,
                order.id,
                order.revision - 1,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrencyError(
                "ORDER_CHANGED_CONCURRENTLY",
                UserMessage(
                    what="Die Änderung wurde nicht gespeichert",
                    why="Der Auftrag wurde zwischenzeitlich an anderer Stelle geändert",
                    unchanged="Der gespeicherte Stand ist unverändert",
                    action="Bitte den Auftrag neu laden und die Änderung wiederholen",
                ),
            )
        self._record_revision(order, payload, reason, now)

    def _record_revision(self, order: Order, payload: str, reason: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO order_revisions (order_id, revision, payload, reason, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (order.id, order.revision, payload, reason, _iso(now)),
        )

    def get(self, order_id: str) -> Order:
        """Liest die aktuelle Revision; ``KeyError``, wenn der Auftrag fehlt."""
        row = self._conn.execute("SELECT payload FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(order_id)
        try:
            return order_from_dict(json.loads(row["payload"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise _damaged("Auftrag", order_id, exc) from exc

    def unassigned_count(self) -> int:
        """Aufträge ohne Profil (Bestand vor Version 2.0 mit Mehrprofil-Fähigkeit)."""
        return int(
            self._conn.execute("SELECT COUNT(*) FROM orders WHERE profile_id = ''").fetchone()[0]
        )

    def assign_unassigned(self, profile_id: str) -> int:
        """Ordnet Altbestand ohne Profil einem Profil zu; die Revision bleibt unverändert."""
        rows = self._conn.execute("SELECT id FROM orders WHERE profile_id = ''").fetchall()
        for row in rows:
            order = replace(self.get(str(row["id"])), profile_id=profile_id)
            self._conn.execute(
                "UPDATE orders SET profile_id = ?, payload = ? WHERE id = ?",
                (profile_id, dumps(order_to_dict(order)), order.id),
            )
        return len(rows)

    def revision(self, order_id: str, revision: int) -> Order:
        """Liest eine frühere Revision."""
        row = self._conn.execute(
            "SELECT payload FROM order_revisions WHERE order_id = ? AND revision = ?",
            (order_id, revision),
        ).fetchone()
        if row is None:
            raise KeyError(f"{order_id}#{revision}")
        return order_from_dict(json.loads(row["payload"]))

    def history(self, order_id: str) -> list[tuple[int, str, str]]:
        """Revisionen als (Revision, Grund, Zeitpunkt), älteste zuerst."""
        rows = self._conn.execute(
            "SELECT revision, reason, changed_at FROM order_revisions WHERE order_id = ? "
            "ORDER BY revision",
            (order_id,),
        ).fetchall()
        return [(int(r["revision"]), str(r["reason"]), str(r["changed_at"])) for r in rows]

    def list_refs(
        self, statuses: Sequence[OrderStatus] = (), *, profile_id: str | None = None
    ) -> list[OrderRef]:
        """Aufträge, optional nach Status und Profil gefiltert, neueste zuerst."""
        query = "SELECT id, status, document_number, revision FROM orders"
        conditions: list[str] = []
        params: tuple[str, ...] = ()
        if statuses:
            conditions.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params += tuple(s.value for s in statuses)
        if profile_id is not None:
            conditions.append("profile_id = ?")
            params += (profile_id,)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        rows = self._conn.execute(query + " ORDER BY created_at DESC, id", params).fetchall()
        return [_order_ref(row) for row in rows]

    def find_by_reference(
        self, customer_key: str, customer_reference: str, *, exclude_id: str
    ) -> list[OrderRef]:
        """Andere Aufträge desselben Kunden mit derselben Kundenbestellnummer."""
        if not customer_key or not customer_reference:
            return []
        rows = self._conn.execute(
            "SELECT id, status, document_number, revision FROM orders "
            "WHERE customer_key = ? AND customer_reference = ? AND id != ? ORDER BY created_at",
            (customer_key, customer_reference, exclude_id),
        ).fetchall()
        return [_order_ref(row) for row in rows]


def _order_ref(row: sqlite3.Row) -> OrderRef:
    return OrderRef(
        id=str(row["id"]),
        status=OrderStatus(row["status"]),
        document_number=row["document_number"],
        revision=int(row["revision"]),
    )


class JournalRepository:
    """Nur anhängendes Journal; Details werden vor dem Speichern bereinigt."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(
        self,
        entity: str,
        entity_id: str,
        event: str,
        now: datetime,
        detail: Mapping[str, object] | None = None,
    ) -> int:
        """Hängt einen Eintrag an und gibt seine laufende Nummer zurück."""
        cleaned = redact_mapping(dict(detail or {}))
        cursor = self._conn.execute(
            "INSERT INTO journal (at, entity, entity_id, event, detail) VALUES (?, ?, ?, ?, ?)",
            (_iso(now), entity, entity_id, event, json.dumps(cleaned, ensure_ascii=False)),
        )
        return int(cursor.lastrowid or 0)

    def entries(self, entity: str, entity_id: str) -> list[tuple[str, str, dict[str, object]]]:
        """Einträge als (Zeitpunkt, Ereignis, Details), älteste zuerst."""
        rows = self._conn.execute(
            "SELECT at, event, detail FROM journal WHERE entity = ? AND entity_id = ? ORDER BY seq",
            (entity, entity_id),
        ).fetchall()
        return [(str(r["at"]), str(r["event"]), json.loads(r["detail"])) for r in rows]


class IdempotencyRepository:
    """Registrierte Schlüssel für Mails und Aufträge."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def register(
        self,
        key: str,
        kind: str,
        now: datetime,
        *,
        mail_id: str | None = None,
        order_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Registriert ``key``; gibt den bisherigen Besitzer (mail_id, order_id) zurück."""
        existing = self.owner(key)
        if existing is not None:
            return existing
        self._conn.execute(
            "INSERT INTO idempotency_keys (key, kind, mail_id, order_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, kind, mail_id, order_id, _iso(now)),
        )
        return (None, None)

    def owner(self, key: str) -> tuple[str | None, str | None] | None:
        """(mail_id, order_id) des registrierten Schlüssels oder ``None``."""
        row = self._conn.execute(
            "SELECT mail_id, order_id FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else (row["mail_id"], row["order_id"])


class CounterRepository:
    """Fortlaufende Zähler, etwa für den Belegnummernkreis."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def next(self, name: str) -> int:
        """Erhöht den Zähler um eins; muss in einer Schreibtransaktion laufen."""
        if not self._conn.in_transaction:
            raise RuntimeError("Zähler dürfen nur innerhalb einer Transaktion erhöht werden")
        self._conn.execute(
            "INSERT INTO counters (name, value) VALUES (?, 0) ON CONFLICT(name) DO NOTHING",
            (name,),
        )
        self._conn.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))
        row = self._conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return int(row["value"])

    def peek(self, name: str) -> int:
        """Aktueller Stand ohne Erhöhung; 0, wenn der Zähler fehlt."""
        row = self._conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return 0 if row is None else int(row["value"])

    def raise_to(self, name: str, value: int) -> None:
        """Hebt einen Zähler auf mindestens ``value`` an (nie nach unten)."""
        self._conn.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = MAX(value, excluded.value)",
            (name, value),
        )


@dataclass(frozen=True, slots=True)
class CatalogVersion:
    """Eine importierte Katalogfassung."""

    version: int
    profile_id: str
    source_name: str
    source_sha256: str
    article_count: int
    imported_at: str
    active: bool
    source_kind: str = ""
    mapping: dict[str, object] = field(default_factory=dict)
    changes: dict[str, object] = field(default_factory=dict)
    backup_file: str = ""
    based_on: int | None = None
    note: str = ""
    number: int = 0


class CatalogRepository:
    """Versionierte Artikelkataloge je Importprofil."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_version(
        self,
        profile_id: str,
        source_name: str,
        source_sha256: str,
        articles: Sequence[Article],
        now: datetime,
        *,
        source_kind: str = "",
        mapping: Mapping[str, object] | None = None,
        changes: Mapping[str, object] | None = None,
        based_on: int | None = None,
        note: str = "",
        backup_file: str = "",
    ) -> int:
        """Speichert eine neue, noch inaktive Fassung und gibt ihre Nummer zurück."""
        number = int(
            self._conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM catalog_versions WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()[0]
        )
        cursor = self._conn.execute(
            "INSERT INTO catalog_versions (profile_id, source_name, source_sha256, "
            "article_count, imported_at, active, source_kind, mapping, changes, based_on, note, "
            "backup_file, number) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                source_name,
                source_sha256,
                len(articles),
                _iso(now),
                source_kind,
                json.dumps(dict(mapping or {}), ensure_ascii=False),
                json.dumps(dict(changes or {}), ensure_ascii=False),
                based_on,
                note,
                backup_file,
                number,
            ),
        )
        version = int(cursor.lastrowid or 0)
        self._conn.executemany(
            "INSERT INTO catalog_articles (version, number, payload) VALUES (?, ?, ?)",
            [(version, a.number, dumps(encode_article(a))) for a in articles],
        )
        return version

    def activate(self, profile_id: str, version: int) -> None:
        """Macht eine Fassung zur aktiven Fassung ihres Profils."""
        self._conn.execute(
            "UPDATE catalog_versions SET active = 0 WHERE profile_id = ? AND active = 1",
            (profile_id,),
        )
        cursor = self._conn.execute(
            "UPDATE catalog_versions SET active = 1 WHERE profile_id = ? AND version = ?",
            (profile_id, version),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"{profile_id}#{version}")

    def versions(self, profile_id: str) -> list[CatalogVersion]:
        """Alle Fassungen eines Profils, neueste zuerst."""
        rows = self._conn.execute(
            "SELECT * FROM catalog_versions WHERE profile_id = ? ORDER BY version DESC",
            (profile_id,),
        ).fetchall()
        return [
            CatalogVersion(
                version=int(r["version"]),
                profile_id=str(r["profile_id"]),
                source_name=str(r["source_name"]),
                source_sha256=str(r["source_sha256"]),
                article_count=int(r["article_count"]),
                imported_at=str(r["imported_at"]),
                active=bool(r["active"]),
                source_kind=str(r["source_kind"]),
                mapping=json.loads(r["mapping"] or "{}"),
                changes=json.loads(r["changes"] or "{}"),
                backup_file=str(r["backup_file"]),
                based_on=None if r["based_on"] is None else int(r["based_on"]),
                note=str(r["note"]),
                number=int(r["number"]),
            )
            for r in rows
        ]

    def articles(self, version: int) -> list[Article]:
        """Artikel einer Fassung, nach Nummer sortiert."""
        rows = self._conn.execute(
            "SELECT payload FROM catalog_articles WHERE version = ? ORDER BY number", (version,)
        ).fetchall()
        return [decode_article(json.loads(r["payload"])) for r in rows]

    def active_version(self, profile_id: str) -> int | None:
        """Nummer der aktiven Fassung oder ``None``."""
        row = self._conn.execute(
            "SELECT version FROM catalog_versions WHERE profile_id = ? AND active = 1",
            (profile_id,),
        ).fetchone()
        return None if row is None else int(row["version"])


@dataclass(frozen=True, slots=True)
class ExportJob:
    """Ein Exportversuch mit vollständigem Bericht."""

    id: str
    order_id: str
    revision: int
    profile_id: str
    mode: ExportMode
    state: ExportJobState
    file_name: str | None
    target_path: str | None
    payload_sha256: str | None
    error: str | None
    adapter_id: str
    adapter_version: str
    spec_version: str
    document_number: str
    order_reference: str
    result: str
    report: dict[str, object]
    created_at: str
    finished_at: str | None


class ExportJobRepository:
    """Tabelle ``export_jobs``; höchstens ein aktiver Produktivjob je Auftrag (Index)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, job: ExportJob, now: datetime) -> None:
        """Legt einen Job an; ein zweiter aktiver Produktivjob scheitert am Index."""
        self._conn.execute(
            "INSERT INTO export_jobs (id, order_id, revision, profile_id, mode, state, file_name, "
            "target_path, payload_sha256, error, adapter_id, adapter_version, spec_version, "
            "document_number, order_reference, result, report, created_at, updated_at, "
            "finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.id,
                job.order_id,
                job.revision,
                job.profile_id,
                job.mode.value,
                job.state.value,
                job.file_name,
                job.target_path,
                job.payload_sha256,
                job.error,
                job.adapter_id,
                job.adapter_version,
                job.spec_version,
                job.document_number,
                job.order_reference,
                job.result,
                json.dumps(job.report, ensure_ascii=False),
                _iso(now),
                _iso(now),
                job.finished_at,
            ),
        )

    def update(
        self,
        job_id: str,
        state: ExportJobState,
        now: datetime,
        *,
        result: str | None = None,
        error: str | None = None,
        report: Mapping[str, object] | None = None,
        finished: bool = False,
    ) -> None:
        """Setzt Zustand und optional Ergebnis, Fehler und Bericht."""
        assignments = ["state = ?", "updated_at = ?"]
        params: list[object] = [state.value, _iso(now)]
        for column, value in (("result", result), ("error", error)):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)
        if report is not None:
            assignments.append("report = ?")
            params.append(json.dumps(dict(report), ensure_ascii=False))
        if finished:
            assignments.append("finished_at = ?")
            params.append(_iso(now))
        cursor = self._conn.execute(
            f"UPDATE export_jobs SET {', '.join(assignments)} WHERE id = ?", (*params, job_id)
        )
        if cursor.rowcount != 1:
            raise KeyError(job_id)

    def get(self, job_id: str) -> ExportJob:
        """Liest einen Job."""
        row = self._conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _export_job(row)

    def for_order(self, order_id: str) -> list[ExportJob]:
        """Alle Jobs eines Auftrags, älteste zuerst."""
        rows = self._conn.execute(
            "SELECT * FROM export_jobs WHERE order_id = ? ORDER BY created_at, id", (order_id,)
        ).fetchall()
        return [_export_job(row) for row in rows]

    def in_states(self, states: Sequence[ExportJobState]) -> list[ExportJob]:
        """Jobs in den genannten Zuständen, etwa unfertige Jobs für die Wiederherstellung."""
        marks = ", ".join("?" for _ in states)
        rows = self._conn.execute(
            f"SELECT * FROM export_jobs WHERE state IN ({marks}) ORDER BY created_at, id",
            tuple(s.value for s in states),
        ).fetchall()
        return [_export_job(row) for row in rows]


def _export_job(row: sqlite3.Row) -> ExportJob:
    return ExportJob(
        id=str(row["id"]),
        order_id=str(row["order_id"]),
        revision=int(row["revision"]),
        profile_id=str(row["profile_id"]),
        mode=ExportMode(row["mode"]),
        state=ExportJobState(row["state"]),
        file_name=row["file_name"],
        target_path=row["target_path"],
        payload_sha256=row["payload_sha256"],
        error=row["error"],
        adapter_id=str(row["adapter_id"]),
        adapter_version=str(row["adapter_version"]),
        spec_version=str(row["spec_version"]),
        document_number=str(row["document_number"]),
        order_reference=str(row["order_reference"]),
        result=str(row["result"]),
        report=json.loads(row["report"]),
        created_at=str(row["created_at"]),
        finished_at=row["finished_at"],
    )
