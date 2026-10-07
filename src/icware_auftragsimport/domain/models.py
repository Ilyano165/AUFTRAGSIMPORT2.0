"""Fachmodelle des Auftrags-Imports.

Alle Modelle sind unveränderlich. Änderungen entstehen über ``dataclasses.replace``;
das macht Revisionen, Rückgängig-Funktion und Vergleiche einfach und sicher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from .countries import postal_code_problem
from .provenance import Field, SourceRef
from .status import OrderStatus


class PaymentMethod(StrEnum):
    """Zahlungsarten, die der Import unterscheidet."""

    INVOICE = "invoice"
    PREPAYMENT = "prepayment"
    CASH_ON_DELIVERY = "cash_on_delivery"
    CASH = "cash"
    DIRECT_DEBIT = "direct_debit"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung für die Oberfläche."""
        return _PAYMENT_LABELS[self]


_PAYMENT_LABELS = {
    PaymentMethod.INVOICE: "Rechnung",
    PaymentMethod.PREPAYMENT: "Vorkasse",
    PaymentMethod.CASH_ON_DELIVERY: "Nachnahme",
    PaymentMethod.CASH: "Barzahlung",
    PaymentMethod.DIRECT_DEBIT: "Lastschrift",
}

ADDRESS_FIELD_LABELS: dict[str, str] = {
    "company": "Firma",
    "department": "Abteilung",
    "name": "Name",
    "street": "Straße",
    "house_number": "Hausnummer",
    "postal_code": "PLZ",
    "city": "Ort",
    "country": "Land",
    "phone": "Telefon",
    "email": "E-Mail",
}

ADDRESS_REQUIRED_FIELDS: tuple[str, ...] = (
    "street",
    "house_number",
    "postal_code",
    "city",
    "country",
)
COMPANY_OR_NAME = "company_or_name"


@dataclass(frozen=True, slots=True)
class Address:
    """Vollständige Anschrift; Land als ISO-3166-Code."""

    company: str = ""
    department: str = ""
    name: str = ""
    street: str = ""
    house_number: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    phone: str = ""
    email: str = ""

    def values(self) -> dict[str, str]:
        """Alle Felder in fester Reihenfolge."""
        return {name: getattr(self, name) for name in ADDRESS_FIELD_LABELS}

    def is_blank(self) -> bool:
        """True, wenn kein einziges Feld gefüllt ist."""
        return not any(value.strip() for value in self.values().values())

    def missing_fields(self) -> tuple[str, ...]:
        """Fehlende Pflichtangaben; ``company_or_name`` steht für Firma oder Name."""
        missing = [name for name in ADDRESS_REQUIRED_FIELDS if not getattr(self, name).strip()]
        if not self.company.strip() and not self.name.strip():
            missing.insert(0, COMPANY_OR_NAME)
        return tuple(missing)

    def postal_code_problem(self) -> str | None:
        """Beschreibt ein zum Land unpassendes PLZ-Format."""
        if not self.postal_code or not self.country:
            return None
        return postal_code_problem(self.country, self.postal_code)

    def is_po_box(self) -> bool:
        """True für Postfachanschriften, an die keine Ware geliefert werden kann."""
        return self.street.strip().casefold().startswith("postfach")

    def summary(self) -> str:
        """Einzeilige Kurzform für Listen und Protokolle ohne Kontaktdaten."""
        head = self.company or self.name
        place = " ".join(part for part in (self.postal_code, self.city) if part)
        return ", ".join(part for part in (head, place) if part)


@dataclass(frozen=True, slots=True)
class Contact:
    """Ansprechpartner des Kunden."""

    salutation: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""

    def display_name(self) -> str:
        """Name für die Anzeige."""
        return " ".join(part for part in (self.first_name, self.last_name) if part)


@dataclass(frozen=True, slots=True)
class Article:
    """Artikel aus dem Katalog des Importprofils."""

    number: str
    name: str
    aliases: tuple[str, ...] = ()
    unit: str = ""
    tax_rate: Decimal | None = None
    price: Decimal | None = None
    active: bool = True


class MatchStatus(StrEnum):
    """Ergebnis der Artikelzuordnung einer Position."""

    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    UNKNOWN = "unknown"
    MANUAL = "manual"


class MatchStrategy(StrEnum):
    """Regel, über die ein Artikel gefunden wurde; Reihenfolge = Priorität."""

    EXPLICIT_NUMBER = "explicit_number"
    ALIAS = "alias"
    NAME = "name"
    NORMALIZED_NAME = "normalized_name"
    FUZZY = "fuzzy"
    MANUAL = "manual"
    NONE = "none"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung für die Oberfläche."""
        return _STRATEGY_LABELS[self]


_STRATEGY_LABELS = {
    MatchStrategy.EXPLICIT_NUMBER: "Artikelnummer",
    MatchStrategy.ALIAS: "Alias",
    MatchStrategy.NAME: "Name",
    MatchStrategy.NORMALIZED_NAME: "normalisierter Name",
    MatchStrategy.FUZZY: "ähnlicher Name",
    MatchStrategy.MANUAL: "manuell",
    MatchStrategy.NONE: "keine",
}


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """Möglicher Artikel mit Bewertung."""

    article: Article
    strategy: MatchStrategy
    score: float


@dataclass(frozen=True, slots=True)
class ArticleMatch:
    """Zuordnung einer Position zu einem Katalogartikel."""

    status: MatchStatus = MatchStatus.UNKNOWN
    article: Article | None = None
    strategy: MatchStrategy = MatchStrategy.NONE
    reason: str = ""
    candidates: tuple[MatchCandidate, ...] = ()
    catalog_version: int | None = None

    @property
    def is_resolved(self) -> bool:
        """True, wenn ein Artikel sicher oder vom Benutzer festgelegt ist."""
        return self.status in (MatchStatus.MATCHED, MatchStatus.MANUAL) and self.article is not None


@dataclass(frozen=True, slots=True)
class OrderLine:
    """Eine Bestellposition mit Herkunft und Zuordnung."""

    position: int
    description: str
    quantity: Field[Decimal] = field(default_factory=Field)
    unit: str = ""
    unit_price: Field[Decimal] = field(default_factory=Field)
    article_hint: Field[str] = field(default_factory=Field)
    match: ArticleMatch = field(default_factory=ArticleMatch)
    source: SourceRef | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Order:
    """Auftrag als Aggregat; jede Änderung erhöht die Revision im Dienst."""

    id: str
    mail_id: str | None = None
    revision: int = 1
    status: OrderStatus = OrderStatus.NEW
    document_number: str | None = None
    customer_key: str = ""
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
    note: str = ""
    lines: tuple[OrderLine, ...] = ()
    acknowledged: frozenset[str] = frozenset()
    profile_id: str = ""

    def effective_delivery_address(self) -> Address | None:
        """Lieferanschrift; bei „wie Rechnung“ die Rechnungsanschrift."""
        if self.delivery_same_as_invoice:
            return self.invoice_address.value
        return self.delivery_address.value


class AttachmentStatus(StrEnum):
    """Ergebnis der Anhangsprüfung."""

    OK = "ok"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Attachment:
    """Anhang einer Mail; der Dateiname dient nur der Anzeige."""

    display_name: str
    declared_type: str
    detected_type: str
    size: int
    sha256: str
    status: AttachmentStatus
    text: str = ""


@dataclass(frozen=True, slots=True)
class MailMetadata:
    """Identität und Kopfdaten einer Mail."""

    id: str
    account_id: str
    folder: str
    uidvalidity: int | None
    uid: int | None
    message_id: str
    content_hash: str
    raw_sha256: str
    sender: str
    sender_name: str
    subject: str
    date_header: datetime | None
    size: int
