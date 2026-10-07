"""Ergebnis der regelbasierten Erkennung einer Mail."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import Address, Contact, OrderLine, PaymentMethod
from .provenance import Field, SourceRef


@dataclass(frozen=True, slots=True)
class ExtractionNote:
    """Hinweis des Parsers, etwa eine Zeile, die wie eine Position aussah."""

    code: str
    message: str
    source: SourceRef | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Alle erkannten Werte mit Herkunft; noch ohne Artikelzuordnung.

    ``customer_email`` ist die Adresse des eigentlichen Bestellers; bei Weiterleitungen
    der ursprüngliche Absender, nicht der Weiterleitende.
    """

    parser_version: str
    customer_email: str = ""
    is_order: Field[bool] = field(default_factory=Field)
    customer_reference: Field[str] = field(default_factory=Field)
    customer_number: Field[str] = field(default_factory=Field)
    vat_id: Field[str] = field(default_factory=Field)
    invoice_address: Field[Address] = field(default_factory=Field)
    delivery_address: Field[Address] = field(default_factory=Field)
    delivery_same_as_invoice: bool = False
    contact: Field[Contact] = field(default_factory=Field)
    order_date: Field[date] = field(default_factory=Field)
    requested_delivery: Field[date] = field(default_factory=Field)
    payment_method: Field[PaymentMethod] = field(default_factory=Field)
    iban: Field[str] = field(default_factory=Field)
    shipping_method: Field[str] = field(default_factory=Field)
    shipping_fee: Field[Decimal] = field(default_factory=Field)
    lines: tuple[OrderLine, ...] = ()
    notes: tuple[ExtractionNote, ...] = ()
