"""LexwareExportAdapter: Auftrag → Bestelldatei im Lexware-openTRANS-Format.

Status: Exportadapter implementiert – Zielsystemvalidierung ausstehend.

Der Adapter erzeugt ausschließlich Elemente, die die Referenzdatei 248090.xml und/oder
die Lexware-Spezifikation „Import von Bestellungen im openTRANS-Format“ (2009) belegen;
Details und offene Fragen stehen in ``lexware_opentrans_order_v1.json``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from ...branding import PRODUCT_NAME, VERSION
from ...config.schema import ImportProfile, PriceMode
from ...domain.countries import COUNTRY_NAMES
from ...domain.findings import Finding, Severity
from ...domain.models import Address, Article, Contact, Order, OrderLine, PaymentMethod
from ...domain.provenance import Confidence, FieldState
from ...parsers.common import split_person_name
from ..model import (
    ExportDocument,
    ExportItem,
    ExportParty,
    ExportPayment,
    PaymentKind,
    PreparedExport,
)
from .serializer import LexwareXmlSerializer, decimal_places, format_money
from .spec import ExportSpec, load_spec

ADAPTER_ID = "lexware_opentrans"
ADAPTER_VERSION = "1.0.0"
CURRENCY = "EUR"
MONEY_PLACES = Decimal("0.01")
MAX_PRICE_PLACES = 4
MAX_QUANTITY_PLACES = 3
PERCENT = Decimal(100)
DELIVERY_METHOD_WARN_LENGTH = 32
CONFIRMED = (FieldState.RECOGNIZED, FieldState.MANUAL)
PAYMENT_CODES: dict[PaymentMethod, tuple[PaymentKind, str, str]] = {
    PaymentMethod.INVOICE: (PaymentKind.CASH, "10", "Rechnung"),
    PaymentMethod.PREPAYMENT: (PaymentKind.CASH, "25", "Vorkasse"),
    PaymentMethod.CASH_ON_DELIVERY: (PaymentKind.CASH, "52", "Nachnahme"),
    PaymentMethod.CASH: (PaymentKind.CASH, "56", "Barzahlung"),
    PaymentMethod.DIRECT_DEBIT: (PaymentKind.ACCOUNT, "54", "Bankeinzug"),
}
PRICE_TYPES = {PriceMode.NET: "net_list", PriceMode.GROSS: "gros_list"}


class _Collector:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.transformations: list[str] = []

    def add(
        self, severity: Severity, code: str, message: str, field: str = "", hint: str = ""
    ) -> None:
        self.findings.append(Finding(code, severity, message, field, hint))

    def error(self, code: str, message: str, field: str = "", hint: str = "") -> None:
        self.add(Severity.ERROR, code, message, field, hint)

    def warning(self, code: str, message: str, field: str = "", hint: str = "") -> None:
        self.add(Severity.WARNING, code, message, field, hint)

    def info(self, code: str, message: str, field: str = "") -> None:
        self.add(Severity.INFO, code, message, field)

    def clean(self, text: str, path: str) -> str:
        if "€" not in text:
            return text
        self.transformations.append(
            f"{path}: „€“ durch „EUR“ ersetzt (Lexware-Spezifikation 3.1.2)"
        )
        return text.replace("€", "EUR")

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.findings)


class LexwareExportAdapter:
    """Bildet Aufträge ab, rendert XML und benennt Dateien."""

    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION

    def __init__(self, spec: ExportSpec | None = None) -> None:
        self.spec = spec or load_spec()

    def prepare(
        self, order: Order, profile: ImportProfile, *, now: datetime, number: str
    ) -> PreparedExport:
        """Interne Exportrepräsentation; ``number`` ist Belegnummer oder Entwurfsnummer."""
        out = _Collector()
        supplier_country = profile.supplier.country
        if not supplier_country:
            out.error(
                "EXP_SUPPLIER_COUNTRY_MISSING",
                "Im Importprofil fehlt das Land des Lieferanten",
                "profile.supplier.country",
                "Lieferantenanschrift im Importprofil vervollständigen",
            )
        invoice = self._invoice(order, out)
        delivery = self._delivery(order, out)
        for address, label in ((invoice, "Rechnungsanschrift"), (delivery, "Lieferanschrift")):
            if (
                address
                and supplier_country
                and address.country
                and address.country != supplier_country
            ):
                out.error(
                    "EXP_FOREIGN_CUSTOMER_UNVERIFIED",
                    f"{label} liegt außerhalb von "
                    f"{COUNTRY_NAMES.get(supplier_country, supplier_country)}; "
                    "die Übergabe des Steuergebiets an Lexware ist noch nicht verifiziert",
                    "invoice_address" if label.startswith("Rechnung") else "delivery_address",
                    "Offene Frage OQ-10; Auftrag bis zur Klärung manuell in Lexware erfassen",
                )
        payment = self._payment(order, out)
        items = self._items(order, out)
        if not order.order_date.has_value:
            out.error("EXP_ORDER_DATE_MISSING", "Bestelldatum fehlt", "order_date")
        delivery_method, shipping_fee = self._shipping(order, out)
        order_date = order.order_date.value
        if out.blocked or invoice is None or delivery is None or payment is None or not order_date:
            return PreparedExport(None, tuple(out.findings), tuple(out.transformations))
        contact = order.contact.value
        document = ExportDocument(
            order_id=order.id,
            revision=order.revision,
            document_number=number,
            external_order_id=self._external_id(order, number, out),
            order_date=order_date,
            generated_at=now,
            generator=f"{PRODUCT_NAME} {VERSION}",
            price_type=PRICE_TYPES[profile.price_mode],
            currency=CURRENCY,
            delivery_party=self._party(delivery, None, "Lieferanschrift", "delivery_party", out),
            invoice_party=self._party(
                invoice,
                contact,
                "Rechnungsanschrift",
                "invoice_party",
                out,
                vat_id=self._vat(order),
            ),
            supplier_party=self._party(profile.supplier, None, "Lieferant", "supplier_party", out),
            payment=payment,
            delivery_method=out.clean(delivery_method, "delivery_method"),
            shipping_fee=shipping_fee,
            remark=out.clean(order.note, "remark"),
            items=items,
        )
        return PreparedExport(
            None if out.blocked else document, tuple(out.findings), tuple(out.transformations)
        )

    @staticmethod
    def _complete(address: Address, label: str, path: str, out: _Collector) -> None:
        missing = address.missing_fields()
        if missing:
            out.error(
                "EXP_ADDRESS_INCOMPLETE", f"{label} unvollständig ({', '.join(missing)})", path
            )
        if problem := address.postal_code_problem():
            out.error("EXP_POSTAL_CODE_INVALID", f"{label}: {problem}", path)

    def _invoice(self, order: Order, out: _Collector) -> Address | None:
        field = order.invoice_address
        if field.value is None:
            out.error("EXP_INVOICE_MISSING", "Rechnungsanschrift fehlt", "invoice_address")
            return None
        if field.state not in CONFIRMED:
            out.error(
                "EXP_INVOICE_UNCONFIRMED",
                "Rechnungsanschrift ist nicht bestätigt",
                "invoice_address",
            )
        self._complete(field.value, "Rechnungsanschrift", "invoice_address", out)
        return field.value

    def _delivery(self, order: Order, out: _Collector) -> Address | None:
        if order.delivery_same_as_invoice:
            return order.invoice_address.value
        field = order.delivery_address
        if field.value is None:
            out.error("EXP_DELIVERY_MISSING", "Lieferanschrift fehlt", "delivery_address")
            return None
        if field.state not in CONFIRMED:
            out.error(
                "EXP_DELIVERY_UNCONFIRMED",
                "Lieferanschrift ist nicht bestätigt",
                "delivery_address",
            )
        self._complete(field.value, "Lieferanschrift", "delivery_address", out)
        if field.value.is_po_box():
            out.warning(
                "EXP_DELIVERY_PO_BOX", "Lieferanschrift ist ein Postfach", "delivery_address"
            )
        out.info(
            "EXP_DELIVERY_ADDRESS_BEHAVIOUR",
            "Abweichende Lieferanschrift wird als BUYER_PARTY übergeben; Lexware ergänzt "
            "Lieferanschriften bestehender Kunden nur und überschreibt sie nie (OQ-07)",
            "delivery_address",
        )
        return field.value

    @staticmethod
    def _payment(order: Order, out: _Collector) -> ExportPayment | None:
        field = order.payment_method
        if field.value is None:
            out.error(
                "EXP_PAYMENT_MISSING",
                "Zahlungsart fehlt; Lexware würde ohne Angabe „Rechnung“ annehmen",
                "payment_method",
            )
            return None
        if field.state not in CONFIRMED:
            out.error(
                "EXP_PAYMENT_UNCONFIRMED", "Zahlungsart ist nicht bestätigt", "payment_method"
            )
        kind, code, label = PAYMENT_CODES[field.value]
        if field.value is PaymentMethod.DIRECT_DEBIT:
            out.info(
                "EXP_NO_BANK_DATA",
                "Bankeinzug wird ohne Bankdaten übergeben; das Lastschriftmandat muss in Lexware "
                "hinterlegt sein (OQ-12)",
                "iban",
            )
        return ExportPayment(kind, code, label)

    @staticmethod
    def _names(
        address: Address, contact: Contact | None, label: str, out: _Collector
    ) -> tuple[str, str]:
        if not address.name:
            return "", ""
        if contact and contact.last_name and contact.display_name() == address.name:
            return contact.first_name, contact.last_name
        first, last, confidence = split_person_name(address.name)
        if confidence is Confidence.UNCERTAIN and first:
            out.warning(
                "EXP_NAME_NOT_SPLIT",
                f"{label}: „{address.name}“ ist nicht eindeutig in Vor- und Nachname teilbar; "
                "der vollständige Name wird als Nachname (NAME2) übergeben",
            )
            return "", address.name
        return first, last

    def _party(
        self,
        address: Address,
        contact: Contact | None,
        label: str,
        path: str,
        out: _Collector,
        *,
        vat_id: str = "",
    ) -> ExportParty:
        first, last = self._names(address, contact, label, out)
        country = COUNTRY_NAMES.get(address.country, "") if address.country else ""
        if address.country and not country:
            out.error(
                "EXP_COUNTRY_UNMAPPED",
                f"{label}: kein Ländername für „{address.country}“ hinterlegt",
                path,
            )
        if address.department:
            out.warning(
                "EXP_DEPARTMENT_DROPPED",
                f"{label}: Abteilung „{address.department}“ hat im Lexware-Format kein Feld und "
                "wird nicht übertragen",
                path,
            )
        phone = address.phone or (contact.phone if contact else "")
        email = address.email or (contact.email if contact else "")
        street = f"{address.street} {address.house_number}".strip()
        return ExportParty(
            company=out.clean(address.company, f"{path}.company"),
            last_name=out.clean(last, f"{path}.last_name"),
            first_name=out.clean(first, f"{path}.first_name"),
            street=out.clean(street, f"{path}.street"),
            city=out.clean(address.city, f"{path}.city"),
            postal_code=address.postal_code,
            country=country,
            phone=phone,
            email=email,
            vat_id=vat_id,
        )

    @staticmethod
    def _vat(order: Order) -> str:
        return order.vat_id.value or "" if order.vat_id.state in CONFIRMED else ""

    def _items(self, order: Order, out: _Collector) -> tuple[ExportItem, ...]:
        if not order.lines:
            out.error("EXP_NO_LINES", "Der Auftrag hat keine Positionen", "lines")
        items = []
        for index, line in enumerate(order.lines):
            item = self._item(index, line, out)
            if item is not None:
                items.append(item)
        catalog = [str(i.line_no) for i in items if i.price_source == "catalog"]
        if catalog:
            out.info(
                "EXP_PRICE_FROM_CATALOG",
                f"Preis aus dem Artikelkatalog für Position(en) {', '.join(catalog)}; Lexware "
                "übernimmt "
                "Preise aus der Datei als manuelle Positionspreise",
                "lines",
            )
        return tuple(items)

    def _item(self, index: int, line: OrderLine, out: _Collector) -> ExportItem | None:
        path = f"lines[{index}]"
        label = f"Position {line.position}"
        article = line.match.article if line.match.is_resolved else None
        if article is None or not article.number.strip():
            out.error(
                "EXP_ARTICLE_UNRESOLVED",
                f"{label}: kein Katalogartikel zugeordnet; Lexware würde die Position verwerfen",
                f"{path}.article",
            )
            return None
        quantity = line.quantity.value
        if quantity is None or line.quantity.state not in CONFIRMED:
            out.error(
                "EXP_QUANTITY_UNCONFIRMED",
                f"{label}: Menge fehlt oder ist nicht bestätigt",
                f"{path}.quantity",
            )
            return None
        if quantity <= 0 or decimal_places(quantity) > MAX_QUANTITY_PLACES:
            out.error(
                "EXP_QUANTITY_INVALID",
                f"{label}: Menge {quantity} ist nicht zulässig",
                f"{path}.quantity",
            )
            return None
        price, source = self._price(line, article, label, path, out)
        if price is None:
            return None
        if article.tax_rate is None or not Decimal(0) <= article.tax_rate < PERCENT:
            out.error(
                "EXP_TAX_MISSING",
                f"{label}: Steuersatz des Artikels {article.number} fehlt oder ist ungültig",
                f"{path}.article",
            )
            return None
        line_amount = (price * quantity).quantize(MONEY_PLACES, ROUND_HALF_UP)
        return ExportItem(
            line_no=index + 1,
            article_number=article.number.strip(),
            quantity=quantity,
            unit_price=price,
            line_amount=line_amount,
            tax_rate=article.tax_rate / PERCENT,
            price_source=source,
        )

    @staticmethod
    def _price(
        line: OrderLine, article: Article, label: str, path: str, out: _Collector
    ) -> tuple[Decimal | None, str]:
        mail = line.unit_price.value if line.unit_price.state in CONFIRMED else None
        if line.unit_price.state is FieldState.NEEDS_REVIEW:
            out.warning(
                "EXP_MAIL_PRICE_UNCONFIRMED",
                f"{label}: Preis aus der Mail ist unbestätigt und wird nicht verwendet",
                f"{path}.unit_price",
            )
        if mail is not None and article.price is not None and mail != article.price:
            out.warning(
                "EXP_PRICE_DIFFERS",
                f"{label}: Preis laut Mail {format_money(mail)} weicht vom Katalogpreis "
                f"{format_money(article.price)} ab; "
                "übertragen wird der Mailpreis",
                f"{path}.unit_price",
            )
        price = mail if mail is not None else article.price
        source = "mail" if mail is not None else "catalog"
        if price is None:
            out.error(
                "EXP_PRICE_MISSING",
                f"{label}: kein Preis aus Mail oder Katalog; das Lexware-Verhalten ohne Preis ist "
                "ungeklärt (OQ-06)",
                f"{path}.unit_price",
                "Preis bestätigen oder Katalog mit Preisen importieren",
            )
            return None, source
        if price < 0 or decimal_places(price) > MAX_PRICE_PLACES:
            out.error(
                "EXP_PRICE_INVALID",
                f"{label}: Preis {price} ist nicht zulässig",
                f"{path}.unit_price",
            )
            return None, source
        if price == 0:
            out.warning("EXP_PRICE_ZERO", f"{label}: Preis ist 0,00", f"{path}.unit_price")
        return price, source

    @staticmethod
    def _shipping(order: Order, out: _Collector) -> tuple[str, Decimal | None]:
        method = (
            order.shipping_method.value or "" if order.shipping_method.state in CONFIRMED else ""
        )
        fee = order.shipping_fee.value if order.shipping_fee.state in CONFIRMED else None
        for field, label, path in (
            (order.shipping_method.state, "Versandart", "shipping_method"),
            (order.shipping_fee.state, "Versandkosten", "shipping_fee"),
        ):
            if field is FieldState.NEEDS_REVIEW:
                out.warning(
                    "EXP_SHIPPING_NOT_TRANSFERRED",
                    f"{label} ist unbestätigt und wird nicht übertragen",
                    path,
                )
        if len(method) > DELIVERY_METHOD_WARN_LENGTH:
            out.warning(
                "EXP_DELIVERY_METHOD_LONG",
                f"Versandart ist länger als {DELIVERY_METHOD_WARN_LENGTH} Zeichen; die Referenz "
                "zeigt eine Kürzung (OQ-13)",
                "shipping_method",
            )
        if fee is not None and fee < 0:
            out.error("EXP_SHIPPING_FEE_INVALID", "Versandkosten sind negativ", "shipping_fee")
        if fee is not None and not method:
            out.warning(
                "EXP_FEE_WITHOUT_METHOD",
                "Versandkosten ohne Versandart; Lexware ordnet Versandkosten über die Versandart "
                "zu",
                "shipping_fee",
            )
        return method, fee

    @staticmethod
    def _external_id(order: Order, number: str, out: _Collector) -> str:
        reference = order.customer_reference
        if reference.value and reference.state in CONFIRMED:
            return out.clean(reference.value, "external_order_id")
        out.info(
            "EXP_ORDER_ID_IS_DOCUMENT_NUMBER",
            f"Keine bestätigte Kundenbestellnummer; ORDER_ID erhält die Belegnummer {number} "
            "(OQ-14)",
            "customer_reference",
        )
        return number

    def file_name(self, document: ExportDocument, *, prefix: str = "") -> str:
        """Dateiname aus der Belegnummer, etwa ``AI-2026-000123.xml``."""
        return f"{prefix}{document.document_number}.xml"

    def render(
        self, document: ExportDocument, encoding: str, *, notation: str | None = None
    ) -> bytes:
        """Erzeugt die XML-Datei; wirft ``UnicodeEncodeError`` bei nicht darstellbaren Zeichen."""
        return LexwareXmlSerializer(self.spec).render(document, encoding, notation=notation)
