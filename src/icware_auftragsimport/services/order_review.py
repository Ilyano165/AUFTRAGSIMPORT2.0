"""Vollständige Prüfung eines Auftrags: Auftragsregeln, Profilregeln und Duplikatwissen.

Eine Stelle für Oberfläche und Mail-Abruf, damit beide dieselben Befunde sehen, auch
„Bestellnummer bereits erfasst“ und „Mail mit identischem Inhalt bereits verarbeitet“.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from ..config.schema import ImportProfile
from ..domain.findings import ValidationResult
from ..domain.models import Order
from ..infrastructure.repositories import MailRepository, OrderRepository
from .idempotency import reference_conflicts
from .profile_rules import profile_findings
from .validation import ValidationContext, validate_order


def duplicate_mail_of(conn: sqlite3.Connection, order: Order) -> str | None:
    """Mail, deren Inhalt die Mail dieses Auftrags bereits hatte (oder ``None``)."""
    if not order.mail_id:
        return None
    try:
        return MailRepository(conn).get(order.mail_id).duplicate_of
    except KeyError:
        return None


def review_order(
    conn: sqlite3.Connection,
    order: Order,
    profile: ImportProfile,
    today: date,
    *,
    duplicate_of: str | None = None,
) -> ValidationResult:
    """Prüft einen Auftrag; ``duplicate_of`` für noch nicht gespeicherte Mails."""
    reference = order.customer_reference.value or ""
    context = ValidationContext(
        today=today,
        reference_conflicts=tuple(
            reference_conflicts(OrderRepository(conn), order.id, order.customer_key, reference)
        ),
        duplicate_mail_of=duplicate_of or duplicate_mail_of(conn, order),
    )
    result = validate_order(order, context)
    extra = tuple(profile_findings(order, profile))
    return ValidationResult(result.findings + extra) if extra else result
