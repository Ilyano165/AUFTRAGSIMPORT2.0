"""Validierung eines Auftrags (Pipeline-Schritt 13).

Regel: Jeder Wert, der exportiert wird und nicht sicher erkannt oder vom Benutzer
bestätigt ist, blockiert den Export. Optionale Angaben erzeugen nur Warnungen.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..domain.countries import country_label, has_postal_rule, is_country_code
from ..domain.findings import Finding, Severity, ValidationResult
from ..domain.models import (
    ADDRESS_FIELD_LABELS,
    COMPANY_OR_NAME,
    Address,
    MatchStatus,
    Order,
    OrderLine,
    PaymentMethod,
)
from ..domain.provenance import Field, FieldState
from ..domain.status import OrderStatus
from ..infrastructure.repositories import OrderRef
from ..parsers.common import EMAIL

MAX_QUANTITY_DECIMALS = 3
_MISSING_LABELS = {**ADDRESS_FIELD_LABELS, COMPANY_OR_NAME: "Firma oder Name"}


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Außenwissen für die Validierung: Datum und mögliche Duplikate."""

    today: date
    reference_conflicts: tuple[OrderRef, ...] = ()
    duplicate_mail_of: str | None = None


def _reason(field: Field[Any]) -> str:
    return field.evidence.describe() if field.evidence and field.evidence.reason else ""


def _with_reason(text: str, field: Field[Any]) -> str:
    reason = _reason(field)
    return f"{text} ({reason})" if reason else text


def _review_or_missing(
    field: Field[Any], path: str, label: str, *, required: bool
) -> list[Finding]:
    code = path.upper()
    if field.state is FieldState.NEEDS_REVIEW:
        severity = Severity.ERROR if required else Severity.WARNING
        message = _with_reason(f"{label} bitte bestätigen", field)
        return [Finding(f"{code}_REVIEW", severity, message, path)]
    if field.state is FieldState.UNKNOWN and required:
        return [Finding(f"{code}_MISSING", Severity.ERROR, f"{label} fehlt", path)]
    return []


def _line_findings(index: int, line: OrderLine) -> list[Finding]:
    prefix = f"lines[{index}]"
    name = f"Position {line.position}"
    findings = _review_or_missing(
        line.quantity, f"{prefix}.quantity", f"{name}: Menge", required=True
    )
    quantity = line.quantity.value
    if quantity is not None and quantity <= 0:
        findings.append(
            Finding(
                "LINE_QUANTITY_INVALID",
                Severity.ERROR,
                f"{name}: Menge muss größer als null sein",
                f"{prefix}.quantity",
            )
        )
    exponent = quantity.as_tuple().exponent if isinstance(quantity, Decimal) else 0
    if isinstance(exponent, int) and -exponent > MAX_QUANTITY_DECIMALS:
        findings.append(
            Finding(
                "LINE_QUANTITY_PRECISION",
                Severity.ERROR,
                f"{name}: Menge hat mehr als {MAX_QUANTITY_DECIMALS} Nachkommastellen",
                f"{prefix}.quantity",
            )
        )
    match = line.match
    if match.status is MatchStatus.UNKNOWN:
        findings.append(
            Finding(
                "LINE_ARTICLE_UNKNOWN",
                Severity.ERROR,
                f"{name}: kein Artikel zugeordnet ({match.reason or 'ohne Angabe'})",
                f"{prefix}.article",
                "Artikel aus dem Katalog auswählen",
            )
        )
    elif match.status is MatchStatus.NEEDS_REVIEW:
        count = len(match.candidates)
        findings.append(
            Finding(
                "LINE_ARTICLE_REVIEW",
                Severity.ERROR,
                f"{name}: Artikel bitte bestätigen ({match.reason}; {count} Vorschläge)",
                f"{prefix}.article",
            )
        )
    article = match.article if match.is_resolved else None
    if article and article.unit and line.unit and article.unit.casefold() != line.unit.casefold():
        findings.append(
            Finding(
                "LINE_UNIT_MISMATCH",
                Severity.WARNING,
                f"{name}: bestellt in „{line.unit}“, Artikel wird in „{article.unit}“ geführt",
                f"{prefix}.unit",
                "Menge und Einheit prüfen",
            )
        )
    if line.unit_price.state is FieldState.NEEDS_REVIEW:
        findings.append(
            Finding(
                "LINE_PRICE_REVIEW",
                Severity.WARNING,
                _with_reason(f"{name}: Preis bitte prüfen", line.unit_price),
                f"{prefix}.unit_price",
            )
        )
    return findings


def _lines(order: Order) -> list[Finding]:
    if not order.lines:
        return [
            Finding(
                "LINES_MISSING",
                Severity.ERROR,
                "Keine Positionen erkannt",
                "lines",
                "Positionen manuell ergänzen oder die Mail ignorieren",
            )
        ]
    findings = []
    positions: dict[str, list[int]] = defaultdict(list)
    for index, line in enumerate(order.lines):
        findings += _line_findings(index, line)
        if line.match.is_resolved and line.match.article is not None:
            positions[line.match.article.number].append(line.position)
    for number, used in sorted(positions.items()):
        if len(used) > 1:
            listed = ", ".join(str(p) for p in used)
            findings.append(
                Finding(
                    "LINE_DUPLICATE_ARTICLE",
                    Severity.WARNING,
                    f"Artikel {number} ist mehrfach bestellt (Positionen {listed})",
                    "lines",
                    "Prüfen, ob die Mehrfachnennung gewollt ist",
                )
            )
    return findings


def _address_content(address: Address, path: str, label: str, *, delivery: bool) -> list[Finding]:
    findings = []
    missing = address.missing_fields()
    if missing:
        listed = ", ".join(_MISSING_LABELS[m] for m in missing)
        findings.append(
            Finding(
                "ADDRESS_INCOMPLETE", Severity.ERROR, f"{label} unvollständig: {listed} fehlt", path
            )
        )
    if address.country and not is_country_code(address.country):
        findings.append(
            Finding(
                "ADDRESS_COUNTRY_INVALID",
                Severity.ERROR,
                f"{label}: „{address.country}“ ist kein Ländercode",
                f"{path}.country",
            )
        )
    elif address.country and not has_postal_rule(address.country):
        findings.append(
            Finding(
                "ADDRESS_POSTAL_UNCHECKED",
                Severity.INFO,
                f"{label}: PLZ-Format für {country_label(address.country)} wird nicht geprüft",
                f"{path}.postal_code",
            )
        )
    if problem := address.postal_code_problem():
        findings.append(
            Finding(
                "ADDRESS_POSTAL_FORMAT",
                Severity.ERROR,
                f"{label}: {problem}",
                f"{path}.postal_code",
            )
        )
    if address.email and not EMAIL.fullmatch(address.email):
        findings.append(
            Finding(
                "ADDRESS_EMAIL_INVALID",
                Severity.WARNING,
                f"{label}: E-Mail „{address.email}“ ist ungültig",
                f"{path}.email",
            )
        )
    if delivery and address.is_po_box():
        findings.append(
            Finding(
                "DELIVERY_PO_BOX",
                Severity.WARNING,
                "Lieferanschrift ist ein Postfach; Ware kann dorthin meist nicht geliefert werden",
                f"{path}.street",
            )
        )
    return findings


def _addresses(order: Order) -> list[Finding]:
    findings = _review_or_missing(
        order.invoice_address, "invoice_address", "Rechnungsanschrift", required=True
    )
    if order.invoice_address.value is not None:
        findings += _address_content(
            order.invoice_address.value,
            "invoice_address",
            "Rechnungsanschrift",
            delivery=order.delivery_same_as_invoice,
        )
    if order.delivery_same_as_invoice:
        return findings
    findings += _review_or_missing(
        order.delivery_address, "delivery_address", "Lieferanschrift", required=True
    )
    if order.delivery_address.value is not None:
        findings += _address_content(
            order.delivery_address.value, "delivery_address", "Lieferanschrift", delivery=True
        )
    return findings


def _payment(order: Order) -> list[Finding]:
    findings = _review_or_missing(
        order.payment_method, "payment_method", "Zahlungsart", required=True
    )
    if order.iban.state is FieldState.NEEDS_REVIEW:
        findings.append(
            Finding(
                "IBAN_REVIEW",
                Severity.WARNING,
                _with_reason("IBAN bitte prüfen", order.iban),
                "iban",
            )
        )
    if order.payment_method.value is PaymentMethod.DIRECT_DEBIT and not order.iban.has_value:
        findings.append(
            Finding(
                "DIRECT_DEBIT_WITHOUT_IBAN",
                Severity.INFO,
                "Lastschrift ohne IBAN in der Mail; Mandat muss in Lexware hinterlegt sein",
                "iban",
            )
        )
    return findings


def _header(order: Order, today: date) -> list[Finding]:
    findings = _review_or_missing(
        order.customer_reference, "customer_reference", "Bestellnummer des Kunden", required=True
    )
    if order.customer_reference.state is FieldState.UNKNOWN:
        findings = [
            Finding(
                "CUSTOMER_REFERENCE_MISSING",
                Severity.WARNING,
                "Keine Bestellnummer des Kunden; Duplikatschutz nur über die Mail",
                "customer_reference",
            )
        ]
    findings += _review_or_missing(order.order_date, "order_date", "Bestelldatum", required=True)
    if order.order_date.evidence and order.order_date.evidence.method == "mail_date_header":
        findings.append(
            Finding(
                "ORDER_DATE_FROM_MAIL",
                Severity.INFO,
                "Bestelldatum aus dem Eingangsdatum der Mail übernommen",
                "order_date",
            )
        )
    findings += _review_or_missing(
        order.requested_delivery, "requested_delivery", "Liefertermin", required=False
    )
    wish = order.requested_delivery.value
    if wish is not None and wish < today:
        findings.append(
            Finding(
                "REQUESTED_DELIVERY_PAST",
                Severity.WARNING,
                f"Liefertermin {wish:%d.%m.%Y} liegt in der Vergangenheit",
                "requested_delivery",
            )
        )
    for field, path, label in (
        (order.shipping_method, "shipping_method", "Versandart"),
        (order.shipping_fee, "shipping_fee", "Versandkosten"),
        (order.vat_id, "vat_id", "USt-IdNr."),
        (order.contact, "contact", "Ansprechpartner"),
    ):
        findings += _review_or_missing(field, path, label, required=False)
    return findings


def _duplicates(order: Order, ctx: ValidationContext) -> list[Finding]:
    findings = []
    for conflict in ctx.reference_conflicts:
        if conflict.status in (OrderStatus.IGNORED,):
            continue
        number = conflict.document_number or conflict.id
        findings.append(
            Finding(
                "DUPLICATE_REFERENCE",
                Severity.ERROR,
                f"Diese Bestellnummer wurde bereits in Auftrag {number} erfasst "
                f"(Status: {conflict.status.label})",
                "customer_reference",
                "Nur freigeben, wenn es wirklich eine zweite Bestellung ist",
                acknowledgeable=True,
            )
        )
    if ctx.duplicate_mail_of:
        findings.append(
            Finding(
                "DUPLICATE_MAIL_CONTENT",
                Severity.ERROR,
                "Eine Mail mit identischem Inhalt wurde bereits verarbeitet",
                "mail",
                "Nur freigeben, wenn der Kunde bewusst erneut bestellt hat",
                acknowledgeable=True,
            )
        )
    return findings


def validate_order(order: Order, ctx: ValidationContext) -> ValidationResult:
    """Alle Befunde zu einem Auftrag, in stabiler Reihenfolge."""
    findings = [
        *_lines(order),
        *_addresses(order),
        *_payment(order),
        *_header(order, ctx.today),
        *_duplicates(order, ctx),
    ]
    return ValidationResult(tuple(findings))


def status_after_validation(result: ValidationResult, acknowledged: frozenset[str]) -> OrderStatus:
    """Bereit zur Freigabe ohne offene Fehler, sonst Prüfung nötig."""
    return OrderStatus.READY if result.is_exportable(acknowledged) else OrderStatus.NEEDS_REVIEW
