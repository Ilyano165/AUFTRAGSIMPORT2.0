"""Wiederherstellung unterbrochener Exporte beim Programmstart.

Regeln: Eine vollständig vorhandene Datei mit passendem Fingerabdruck gilt als exportiert.
Eine fehlende Datei gilt als nicht exportiert (Auftrag „Fehler“, erneute Freigabe nötig).
Eine abweichende Datei macht den Auftrag „unklar“. Es wird nie automatisch neu exportiert.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from ..domain.ports import Clock
from ..domain.status import ExportJobState, OrderStatus, can_transition
from ..infrastructure.atomic import leftover_temp_files
from ..infrastructure.db import transaction
from ..infrastructure.repositories import (
    ExportJob,
    ExportJobRepository,
    JournalRepository,
    OrderRepository,
)

UNFINISHED = (ExportJobState.PENDING, ExportJobState.WRITING, ExportJobState.COMMITTED)


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    """Eine durchgeführte Wiederherstellung."""

    job_id: str
    order_id: str
    previous_state: ExportJobState
    new_state: ExportJobState
    message: str


class ExportRecovery:
    """Löst unfertige Exportjobs eindeutig auf."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock

    def run(self) -> list[RecoveryAction]:
        """Bearbeitet alle unfertigen Jobs und entfernt verwaiste Zwischendateien."""
        actions = []
        for job in ExportJobRepository(self._conn).in_states(UNFINISHED):
            state, status, message = self._decide(job)
            self._apply(job, state, status, message)
            actions.append(RecoveryAction(job.id, job.order_id, job.state, state, message))
            if job.target_path:
                for leftover in leftover_temp_files(Path(job.target_path).parent):
                    with contextlib.suppress(OSError):
                        leftover.unlink()
        return actions

    @staticmethod
    def _decide(job: ExportJob) -> tuple[ExportJobState, OrderStatus, str]:
        target = Path(job.target_path) if job.target_path else None
        if job.state is ExportJobState.PENDING or target is None or not target.exists():
            return (
                ExportJobState.FAILED,
                OrderStatus.FAILED,
                "Export vor dem Schreiben unterbrochen; keine Datei vorhanden. Auftrag kann nach "
                "Prüfung erneut freigegeben werden",
            )
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest == job.payload_sha256:
            return (
                ExportJobState.DONE,
                OrderStatus.EXPORTED,
                "Datei vollständig vorhanden; Exportstatus nachgetragen",
            )
        return (
            ExportJobState.UNCLEAR,
            OrderStatus.UNCLEAR,
            "Datei vorhanden, Inhalt weicht ab; bitte in Lexware prüfen und manuell auflösen",
        )

    def _apply(
        self, job: ExportJob, state: ExportJobState, status: OrderStatus, message: str
    ) -> None:
        now = self._clock.now()
        orders = OrderRepository(self._conn)
        with transaction(self._conn):
            ExportJobRepository(self._conn).update(
                job.id, state, now, result="recovered", error=message, finished=True
            )
            order = orders.get(job.order_id)
            if can_transition(order.status, status):
                orders.save(
                    replace(order, status=status, revision=order.revision + 1), message, now
                )
            JournalRepository(self._conn).append(
                "order", job.order_id, "export_recovered", now,
                {"job_id": job.id, "state": state.value, "note": message},
            )  # fmt: skip
