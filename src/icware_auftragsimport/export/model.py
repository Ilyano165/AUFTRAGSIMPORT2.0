"""Interne Exportrepräsentation, unabhängig vom Zielformat.

Ein Adapter bildet einen Auftrag zuerst auf diese Struktur ab. Erst wenn sie vollständig
und geprüft ist, wird daraus eine Datei. So bleibt die Kernanwendung frei von einem
bestimmten Exportformat.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from ..domain.findings import Finding


class PaymentKind(StrEnum):
    """Art der Zahlungsangabe im Zielformat."""

    CASH = "cash"
    ACCOUNT = "account"


@dataclass(frozen=True, slots=True)
class ExportParty:
    """Anschrift in der Gliederung des Zielformats."""

    company: str
    last_name: str
    first_name: str
    street: str
    city: str
    postal_code: str
    country: str
    phone: str = ""
    fax: str = ""
    email: str = ""
    vat_id: str = ""


@dataclass(frozen=True, slots=True)
class ExportPayment:
    """Zahlungsart mit Code des Zielformats."""

    kind: PaymentKind
    code: str
    label: str


@dataclass(frozen=True, slots=True)
class ExportItem:
    """Eine Position, vollständig aufgelöst."""

    line_no: int
    article_number: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal
    tax_rate: Decimal
    price_source: str


@dataclass(frozen=True, slots=True)
class ExportDocument:
    """Vollständige, formatneutrale Darstellung eines zu exportierenden Auftrags."""

    order_id: str
    revision: int
    document_number: str
    external_order_id: str
    order_date: date
    generated_at: datetime
    generator: str
    price_type: str
    currency: str
    delivery_party: ExportParty
    invoice_party: ExportParty
    supplier_party: ExportParty
    payment: ExportPayment
    delivery_method: str
    shipping_fee: Decimal | None
    remark: str
    items: tuple[ExportItem, ...]

    def texts(self) -> Iterator[tuple[str, str]]:
        """Alle Freitexte mit Pfad, etwa für Encoding- und Zeichenprüfungen."""
        yield "external_order_id", self.external_order_id
        yield "generator", self.generator
        for name, party in (
            ("delivery_party", self.delivery_party),
            ("invoice_party", self.invoice_party),
            ("supplier_party", self.supplier_party),
        ):
            for spec_field in fields(party):
                yield f"{name}.{spec_field.name}", str(getattr(party, spec_field.name))
        yield "delivery_method", self.delivery_method
        yield "remark", self.remark
        for line in self.items:
            yield f"items[{line.line_no}].article_number", line.article_number


@dataclass(frozen=True, slots=True)
class PreparedExport:
    """Ergebnis der Abbildung: Dokument (falls bildbar), Befunde und Umformungen."""

    document: ExportDocument | None
    findings: tuple[Finding, ...]
    transformations: tuple[str, ...] = ()
