"""Zustände und erlaubte Übergänge für Mails, Aufträge und Exportjobs."""

from __future__ import annotations

from enum import StrEnum

from .errors import TransitionError, UserMessage


class OrderStatus(StrEnum):
    """Lebenszyklus eines Auftrags."""

    NEW = "new"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    APPROVED = "approved"
    EXPORTING = "exporting"
    EXPORTED = "exported"
    IGNORED = "ignored"
    FAILED = "failed"
    UNCLEAR = "unclear"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung für die Oberfläche."""
        return _ORDER_STATUS_LABELS[self]


_ORDER_STATUS_LABELS = {
    OrderStatus.NEW: "Neu",
    OrderStatus.NEEDS_REVIEW: "Prüfung nötig",
    OrderStatus.READY: "Bereit zur Freigabe",
    OrderStatus.APPROVED: "Freigegeben",
    OrderStatus.EXPORTING: "Export läuft",
    OrderStatus.EXPORTED: "Exportiert",
    OrderStatus.IGNORED: "Ignoriert",
    OrderStatus.FAILED: "Fehler",
    OrderStatus.UNCLEAR: "Status unklar",
}

_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset(
        {OrderStatus.NEEDS_REVIEW, OrderStatus.READY, OrderStatus.IGNORED, OrderStatus.FAILED}
    ),
    OrderStatus.NEEDS_REVIEW: frozenset(
        {OrderStatus.NEEDS_REVIEW, OrderStatus.READY, OrderStatus.IGNORED}
    ),
    OrderStatus.READY: frozenset(
        {OrderStatus.READY, OrderStatus.APPROVED, OrderStatus.NEEDS_REVIEW, OrderStatus.IGNORED}
    ),
    OrderStatus.APPROVED: frozenset(
        {OrderStatus.EXPORTING, OrderStatus.NEEDS_REVIEW, OrderStatus.READY}
    ),
    OrderStatus.EXPORTING: frozenset(
        {OrderStatus.EXPORTED, OrderStatus.FAILED, OrderStatus.UNCLEAR}
    ),
    OrderStatus.FAILED: frozenset(
        {OrderStatus.APPROVED, OrderStatus.NEEDS_REVIEW, OrderStatus.READY}
    ),
    OrderStatus.UNCLEAR: frozenset({OrderStatus.EXPORTED, OrderStatus.FAILED}),
    OrderStatus.IGNORED: frozenset({OrderStatus.NEEDS_REVIEW, OrderStatus.READY}),
    OrderStatus.EXPORTED: frozenset(),
}


def can_transition(source: OrderStatus, target: OrderStatus) -> bool:
    """Prüft, ob der Ablauf den Wechsel von ``source`` nach ``target`` erlaubt."""
    return target in _ORDER_TRANSITIONS[source]


def ensure_transition(source: OrderStatus, target: OrderStatus) -> None:
    """Wirft ``TransitionError``, wenn der Wechsel nicht erlaubt ist."""
    if can_transition(source, target):
        return
    raise TransitionError(
        "ORDER_TRANSITION_NOT_ALLOWED",
        UserMessage(
            what=f"Der Auftrag kann nicht von „{source.label}“ nach „{target.label}“ wechseln",
            why="Dieser Statuswechsel ist im Ablauf nicht vorgesehen",
            unchanged="Der Auftrag wurde nicht verändert",
            action="Bitte die Liste aktualisieren und den Vorgang erneut starten",
        ),
    )


class MailState(StrEnum):
    """Verarbeitungsstand einer Mail."""

    DISCOVERED = "discovered"
    LOADED = "loaded"
    EXTRACTED = "extracted"
    NOT_AN_ORDER = "not_an_order"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class ExportMode(StrEnum):
    """Echter Export, Testexport in separaten Ordner oder Probelauf ohne Datei."""

    LIVE = "live"
    TEST = "test"
    DRY_RUN = "dry_run"


class ExportJobState(StrEnum):
    """Schritte eines Exportjobs; jeder wird vor seinem Seiteneffekt gespeichert."""

    PENDING = "pending"
    ARCHIVING = "archiving"
    ARCHIVED = "archived"
    ARCHIVE_FAILED = "archive_failed"
    WRITING = "writing"
    COMMITTED = "committed"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCLEAR = "unclear"
