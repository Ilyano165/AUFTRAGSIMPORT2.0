"""Export-Engine (Pipeline-Schritte 15, 17, 18): Vorschau, Probelauf, Test- und Produktivexport.

Jeder Zustand wird in der Datenbank gespeichert, bevor seine Wirkung eintritt. Ein Absturz
hinterlässt daher immer einen Job, den die Wiederherstellung eindeutig auflösen kann;
nie wird automatisch ein zweites Mal exportiert.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from ..config.schema import ImportProfile
from ..domain.errors import UserMessage
from ..domain.findings import Severity, ValidationResult
from ..domain.models import Order
from ..domain.ports import Clock
from ..domain.status import ExportJobState, ExportMode, OrderStatus, ensure_transition
from ..export.lexware.adapter import LexwareExportAdapter
from ..export.model import PreparedExport
from ..export.validator import ExportValidator
from ..infrastructure.atomic import atomic_create_in
from ..infrastructure.db import transaction
from ..infrastructure.logging_setup import get_logger
from ..infrastructure.repositories import (
    ExportJob,
    ExportJobRepository,
    JournalRepository,
    OrderRepository,
)
from ..security.fs import DirectoryIdentity, UnsafePathError, inspect_directory, safe_join

TEST_FILE_PREFIX = "TEST_"
DRAFT_PREFIX = "ENTWURF-"
DRAFT_ID_LENGTH = 8
PIN_PURPOSE = "export"
MODE_LABELS = {
    ExportMode.LIVE: "Produktivexport",
    ExportMode.TEST: "Testexport",
    ExportMode.DRY_RUN: "Probelauf",
}
_log = get_logger("export")


class ExportResult(StrEnum):
    """Ergebnis eines Exportversuchs."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class ExportPreview:
    """XML-Vorschau mit allen Befunden; es wird nichts gespeichert."""

    file_name: str | None
    xml: str | None
    validation: ValidationResult
    transformations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExportReport:
    """Nachvollziehbarer Bericht jedes Exportversuchs."""

    job_id: str
    order_id: str
    revision: int
    document_number: str
    order_reference: str
    mode: ExportMode
    result: ExportResult
    exported_at: str
    export_path: str | None
    xml_sha256: str | None
    adapter_id: str
    adapter_version: str
    spec_version: str
    spec_sha256: str
    encoding: str
    message: str
    findings: tuple[str, ...] = ()
    transformations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Darstellung für Datenbank und Diagnosebericht."""
        return {
            "job_id": self.job_id,
            "order_id": self.order_id,
            "revision": self.revision,
            "document_number": self.document_number,
            "order_reference": self.order_reference,
            "mode": self.mode.value,
            "result": self.result.value,
            "exported_at": self.exported_at,
            "export_path": self.export_path,
            "xml_sha256": self.xml_sha256,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "spec_version": self.spec_version,
            "spec_sha256": self.spec_sha256,
            "encoding": self.encoding,
            "message": self.message,
            "findings": list(self.findings),
            "transformations": list(self.transformations),
        }


class ExportBlocked(Exception):
    """Interner Abbruch vor dem Schreiben, mit Benutzermeldung."""

    def __init__(self, message: UserMessage) -> None:
        super().__init__(message.render())
        self.message = message


@dataclass(frozen=True, slots=True)
class _Built:
    prepared: PreparedExport
    xml: bytes | None
    file_name: str | None
    validation: ValidationResult


@dataclass(frozen=True, slots=True)
class _Attempt:
    job_id: str
    order: Order
    profile: ImportProfile
    mode: ExportMode
    built: _Built


def _blocked(what: str, why: str, action: str) -> ExportBlocked:
    return ExportBlocked(
        UserMessage(what=what, why=why, unchanged="Es wurde keine Datei geschrieben", action=action)
    )


def separated(export_dir: Path, test_dir: Path) -> bool:
    """True, wenn Test- und Produktivordner verschieden sind und nicht ineinander liegen."""
    live, test = export_dir.resolve(), test_dir.resolve()
    return live != test and live not in test.parents and test not in live.parents


class ExportService:
    """Führt Exporte aus und schreibt für jeden Versuch einen Bericht."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        adapter: LexwareExportAdapter | None = None,
        *,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._adapter = adapter or LexwareExportAdapter()
        self._validator = ExportValidator(self._adapter.spec)
        self._new_id = id_factory

    def preview(self, order: Order, profile: ImportProfile) -> ExportPreview:
        """Erzeugt XML und Befunde ohne jede Speicherung."""
        built = self._build(order, profile, ExportMode.DRY_RUN)
        xml = built.xml.decode(profile.export_encoding) if built.xml is not None else None
        return ExportPreview(built.file_name, xml, built.validation, built.prepared.transformations)

    def run(self, order_id: str, profile: ImportProfile, mode: ExportMode) -> ExportReport:
        """Exportiert einen Auftrag im gewählten Modus und speichert den Bericht."""
        order = OrderRepository(self._conn).get(order_id)
        empty = _Built(PreparedExport(None, ()), None, None, ValidationResult())
        attempt = _Attempt(self._new_id(), order, profile, mode, empty)
        try:
            target_dir = self._check_preconditions(order, profile, mode)
        except ExportBlocked as blocked:
            return self._record(attempt, ExportResult.BLOCKED, None, blocked.message.render())
        attempt = replace(attempt, built=self._build(order, profile, mode))
        built = attempt.built
        if built.validation.errors() or built.xml is None or built.file_name is None:
            return self._finish_blocked(attempt)
        if target_dir is None:
            message = "Probelauf erfolgreich; es wurde keine Datei geschrieben"
            return self._record(attempt, ExportResult.SUCCESS, None, message)
        try:
            target = safe_join(target_dir, built.file_name)
        except UnsafePathError as exc:
            return self._record(attempt, ExportResult.BLOCKED, None, str(exc))
        if mode is ExportMode.TEST:
            return self._write_test(attempt, built.xml, target)
        return self._write_live(attempt, built.xml, target)

    def _check_preconditions(
        self, order: Order, profile: ImportProfile, mode: ExportMode
    ) -> Path | None:
        if mode is ExportMode.DRY_RUN:
            return None
        live = Path(profile.export_dir) if profile.export_dir else None
        test = Path(profile.test_export_dir) if profile.test_export_dir else None
        if mode is ExportMode.TEST:
            test_dir = self._test_dir(live, test)
            self._inspect(test_dir, "Testordner", profile)
            return test_dir
        if not profile.target_validated:
            raise _blocked(
                "Produktivexport ist für dieses Importprofil gesperrt",
                "Exportadapter implementiert – Zielsystemvalidierung ausstehend; die Prüfung mit "
                "der Lexware-Installation des Kunden ist noch nicht bestätigt",
                "Integrationstestplan Lexware durchführen und das Ergebnis im Importprofil "
                "bestätigen",
            )
        if order.status is not OrderStatus.APPROVED or not order.document_number:
            raise _blocked(
                "Der Auftrag ist nicht freigegeben",
                f"Status „{order.status.label}“; nur freigegebene Aufträge mit Belegnummer "
                "werden produktiv exportiert",
                "Auftrag prüfen und freigeben",
            )
        if live is None or not live.is_dir():
            raise _blocked(
                "Der Lexware-Importordner ist nicht erreichbar",
                f"Ordner „{profile.export_dir or '(nicht eingerichtet)'}“ existiert nicht",
                "Exportordner im Importprofil prüfen",
            )
        if test is not None and not separated(live, test):
            raise _blocked(
                "Test- und Produktivordner sind nicht getrennt",
                "Der Testordner ist gleich dem Exportordner oder liegt darin",
                "Getrennte Ordner im Importprofil einrichten",
            )
        self._verify_pin(profile.id, self._inspect(live, "Lexware-Importordner", profile))
        return live

    @staticmethod
    def _inspect(directory: Path, purpose: str, profile: ImportProfile) -> DirectoryIdentity:
        try:
            return inspect_directory(
                str(directory), purpose=purpose, allow_network=profile.export_dir_network_allowed
            )
        except UnsafePathError as exc:
            raise _blocked(
                f"{purpose} wurde aus Sicherheitsgründen abgelehnt",
                str(exc),
                "Ordner im Importprofil prüfen",
            ) from exc

    def _verify_pin(self, profile_id: str, identity: DirectoryIdentity) -> None:
        """Erster Produktivexport bestätigt den Ordner; danach muss er gleich bleiben."""
        row = self._conn.execute(
            "SELECT path, device, inode FROM directory_pins WHERE profile_id = ? AND purpose = ?",
            (profile_id, PIN_PURPOSE),
        ).fetchone()
        now = self._clock.now()
        if row is None:
            with transaction(self._conn):
                self._conn.execute(
                    "INSERT INTO directory_pins (profile_id, purpose, path, device, inode, "
                    "pinned_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (profile_id, PIN_PURPOSE, identity.path, identity.device, identity.inode,
                     now.isoformat()),
                )  # fmt: skip
                JournalRepository(self._conn).append(
                    "profile", profile_id, "export_dir_pinned", now, {}
                )
            return
        if (row[0], int(row[1]), int(row[2])) != (identity.path, identity.device, identity.inode):
            raise _blocked(
                "Der Lexware-Importordner hat sich verändert",
                "Die Ordnerkennung weicht von der bestätigten ab (anderer Ordner, anderes Laufwerk "
                "oder ersetzte Verknüpfung)",
                "Ordner prüfen und die Ordnerbestätigung im Importprofil zurücksetzen",
            )

    def reset_directory_pin(self, profile_id: str) -> None:
        """Administrator bestätigt einen geänderten Exportordner neu."""
        now = self._clock.now()
        with transaction(self._conn):
            self._conn.execute(
                "DELETE FROM directory_pins WHERE profile_id = ? AND purpose = ?",
                (profile_id, PIN_PURPOSE),
            )
            JournalRepository(self._conn).append(
                "profile", profile_id, "export_dir_pin_reset", now, {}
            )

    @staticmethod
    def _test_dir(live: Path | None, test: Path | None) -> Path:
        if test is None or not test.is_dir():
            raise _blocked(
                "Der Testexport ist nicht möglich",
                "Es ist kein vorhandener Testordner eingerichtet; der Lexware-Importordner wird "
                "für Tests nie verwendet",
                "Einen separaten Testordner im Importprofil eintragen",
            )
        if live is not None and not separated(live, test):
            raise _blocked(
                "Der Testexport wurde verweigert",
                "Der Testordner ist gleich dem Lexware-Importordner oder liegt darin bzw. darüber",
                "Einen vollständig getrennten Testordner einrichten",
            )
        return test

    def _build(self, order: Order, profile: ImportProfile, mode: ExportMode) -> _Built:
        number = order.document_number or f"{DRAFT_PREFIX}{order.id[:DRAFT_ID_LENGTH].upper()}"
        prepared = self._adapter.prepare(order, profile, now=self._clock.now(), number=number)
        xml: bytes | None = None
        file_name: str | None = None
        if prepared.document is not None:
            prefix = TEST_FILE_PREFIX if mode is ExportMode.TEST else ""
            file_name = self._adapter.file_name(prepared.document, prefix=prefix)
            try:
                xml = self._adapter.render(prepared.document, profile.export_encoding)
            except UnicodeEncodeError:
                xml = None
        validation = self._validator.validate(
            prepared, xml=xml, file_name=file_name, encoding=profile.export_encoding
        )
        return _Built(prepared, xml, file_name, validation)

    def _write_test(self, attempt: _Attempt, xml: bytes, target: Path) -> ExportReport:
        try:
            atomic_create_in(target.parent, target.name, xml)
        except FileExistsError:
            message = f"Testdatei „{target.name}“ existiert bereits und wird nicht überschrieben"
            return self._record(attempt, ExportResult.BLOCKED, None, message)
        except OSError as exc:
            return self._record(attempt, ExportResult.FAILED, None, _write_error(exc).render())
        return self._record(
            attempt, ExportResult.SUCCESS, target, f"Testdatei geschrieben: {target}"
        )

    def _write_live(self, attempt: _Attempt, xml: bytes, target: Path) -> ExportReport:
        orders = OrderRepository(self._conn)
        jobs = ExportJobRepository(self._conn)
        report = self._report(attempt, ExportResult.SUCCESS, target, "Export läuft")
        try:
            with transaction(self._conn):
                jobs.insert(self._job(attempt, report, ExportJobState.PENDING), self._clock.now())
                order = self._set_status(
                    orders, attempt.order, OrderStatus.EXPORTING, "Export gestartet"
                )
        except sqlite3.IntegrityError:
            message = (
                "Für diesen Auftrag läuft bereits ein Export oder er wurde bereits exportiert; "
                "ein zweiter Export ist ausgeschlossen"
            )
            retry = replace(attempt, job_id=self._new_id())
            return self._record(retry, ExportResult.BLOCKED, None, message)
        with transaction(self._conn):
            jobs.update(attempt.job_id, ExportJobState.WRITING, self._clock.now())
        try:
            atomic_create_in(target.parent, target.name, xml)
        except OSError as exc:
            failed = replace(report, result=ExportResult.FAILED, export_path=None,
                             xml_sha256=None, message=_failure(exc, target).render())  # fmt: skip
            return self._finalize(
                attempt.job_id, order, ExportJobState.FAILED, OrderStatus.FAILED, failed
            )
        done = replace(report, message=f"Exportiert nach {target}")
        return self._finalize(
            attempt.job_id, order, ExportJobState.DONE, OrderStatus.EXPORTED, done
        )

    def _finalize(
        self,
        job_id: str,
        order: Order,
        state: ExportJobState,
        status: OrderStatus,
        report: ExportReport,
    ) -> ExportReport:
        now = self._clock.now()
        with transaction(self._conn):
            ExportJobRepository(self._conn).update(
                job_id,
                state,
                now,
                result=report.result.value,
                report=report.to_dict(),
                finished=True,
            )
            self._set_status(OrderRepository(self._conn), order, status, MODE_LABELS[report.mode])
            JournalRepository(self._conn).append(
                "order", order.id, f"export_{report.result.value}", now,
                {"job_id": job_id, "mode": report.mode.value, "file": report.export_path or ""},
            )  # fmt: skip
        _log.info(
            "Export %s: Auftrag %s, Ergebnis %s", report.mode.value, order.id, report.result.value
        )
        return report

    def _set_status(
        self, orders: OrderRepository, order: Order, status: OrderStatus, reason: str
    ) -> Order:
        ensure_transition(order.status, status)
        updated = replace(order, status=status, revision=order.revision + 1)
        orders.save(updated, reason, self._clock.now())
        return updated

    def _report(
        self, attempt: _Attempt, result: ExportResult, target: Path | None, message: str
    ) -> ExportReport:
        order, profile, built = attempt.order, attempt.profile, attempt.built
        shown = [f for f in built.validation.findings if f.severity is not Severity.INFO]
        return ExportReport(
            job_id=attempt.job_id,
            order_id=order.id,
            revision=order.revision,
            document_number=order.document_number or "",
            order_reference=order.customer_reference.value or "",
            mode=attempt.mode,
            result=result,
            exported_at=self._clock.now().isoformat(timespec="seconds"),
            export_path=str(target) if target else None,
            xml_sha256=hashlib.sha256(built.xml).hexdigest() if built.xml and target else None,
            adapter_id=self._adapter.adapter_id,
            adapter_version=self._adapter.adapter_version,
            spec_version=self._adapter.spec.version,
            spec_sha256=self._adapter.spec.sha256,
            encoding=profile.export_encoding,
            message=message,
            findings=tuple(f"{f.severity.label}: {f.message}" for f in shown),
            transformations=built.prepared.transformations,
        )

    @staticmethod
    def _job(attempt: _Attempt, report: ExportReport, state: ExportJobState) -> ExportJob:
        built = attempt.built
        return ExportJob(
            id=report.job_id,
            order_id=report.order_id,
            revision=report.revision,
            profile_id=attempt.profile.id,
            mode=report.mode,
            state=state,
            file_name=built.file_name,
            target_path=report.export_path,
            payload_sha256=hashlib.sha256(built.xml).hexdigest() if built.xml else None,
            error=None,
            adapter_id=report.adapter_id,
            adapter_version=report.adapter_version,
            spec_version=report.spec_version,
            document_number=report.document_number,
            order_reference=report.order_reference,
            result=report.result.value,
            report=report.to_dict(),
            created_at="",
            finished_at=None,
        )

    def _finish_blocked(self, attempt: _Attempt) -> ExportReport:
        count = len(attempt.built.validation.errors())
        message = UserMessage(
            f"{MODE_LABELS[attempt.mode]} wurde nicht ausgeführt",
            f"Die Exportprüfung hat {count} Fehler gefunden",
            "Es wurde keine Datei geschrieben",
            "Fehler in der Validierungsübersicht beheben und erneut exportieren",
        )
        return self._record(attempt, ExportResult.BLOCKED, None, message.render())

    def _record(
        self, attempt: _Attempt, result: ExportResult, target: Path | None, message: str
    ) -> ExportReport:
        report = self._report(attempt, result, target, message)
        state = ExportJobState.DONE if result is ExportResult.SUCCESS else ExportJobState.FAILED
        now = self._clock.now()
        job = replace(self._job(attempt, report, state), finished_at=now.isoformat())
        with transaction(self._conn):
            ExportJobRepository(self._conn).insert(job, now)
            JournalRepository(self._conn).append(
                "order", attempt.order.id, f"export_{result.value}", now,
                {
                    "job_id": attempt.job_id,
                    "mode": attempt.mode.value,
                    "file": report.export_path or "",
                },
            )  # fmt: skip
        return report


def _failure(exc: OSError, target: Path) -> UserMessage:
    if isinstance(exc, FileExistsError):
        return UserMessage(
            "Export wurde abgebrochen",
            f"Die Datei „{target.name}“ existiert bereits im Exportordner",
            "Es wurde nichts überschrieben",
            "Vorhandene Datei in Lexware prüfen; Auftrag danach erneut freigeben",
        )
    return _write_error(exc)


def _write_error(exc: OSError) -> UserMessage:
    return UserMessage(
        "Export wurde abgebrochen",
        f"Die Datei konnte nicht geschrieben werden: {exc.strerror or type(exc).__name__}",
        "Im Exportordner ist keine Datei entstanden; die Zwischendatei wurde entfernt",
        "Zugriffsrechte und freien Speicher des Exportordners prüfen",
    )
