"""Testhilfen: komplette Erkennung einer Mail ohne Datenbank."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from icware_auftragsimport.config.schema import ImportProfile
from icware_auftragsimport.domain.extraction import ExtractionResult
from icware_auftragsimport.domain.findings import ValidationResult
from icware_auftragsimport.domain.models import (
    Address,
    Article,
    ArticleMatch,
    Contact,
    MatchStatus,
    MatchStrategy,
    Order,
    OrderLine,
    PaymentMethod,
)
from icware_auftragsimport.domain.provenance import Confidence, Evidence, Field
from icware_auftragsimport.domain.status import OrderStatus
from icware_auftragsimport.parsers.common import ParseContext
from icware_auftragsimport.parsers.engine import ExtractionEngine
from icware_auftragsimport.parsers.text import html_to_text, normalize
from icware_auftragsimport.services.matching import ArticleMatcher, Catalog
from icware_auftragsimport.services.order_factory import build_order
from icware_auftragsimport.services.validation import ValidationContext, validate_order

TODAY = date(2026, 9, 30)
MAIL_DATE = date(2026, 9, 29)
SUPPLIER = Address(
    company="Beispiel Großhandel GmbH",
    street="Lagerweg",
    house_number="4",
    postal_code="51570",
    city="Windeck",
    country="DE",
)
PROFILE = ImportProfile(id="standard", name="Standard", supplier=SUPPLIER)
PROFILE_WITH_DEFAULT = ImportProfile(
    id="standard", name="Standard", supplier=SUPPLIER, default_payment_method=PaymentMethod.INVOICE
)
ARTICLES = (
    Article("YT11YBOR01", "YelloBlade Orange", ("Blade Orange",), "Stk", Decimal("19")),
    Article("YT11YBGR01", "YelloBlade Grün", ("Blade Grün",), "Stk", Decimal("19")),
    Article("YT15FILZ02", "Filzstreifen", ("Filz",), "Stk", Decimal("19")),
    Article("YT20RAK01", "Rakel Gold", (), "Stk", Decimal("19")),
    Article(
        "GT-1001", "Mineralwasser Classic 12x1,0l", ("Wasser Classic",), "Kiste", Decimal("19")
    ),
    Article("GT-1002", "Mineralwasser Medium 12x1,0l", ("Wasser Medium",), "Kiste", Decimal("19")),
    Article("GT-2001", "Apfelschorle 24x0,33l", ("Apfelschorle",), "Kiste", Decimal("19")),
    Article("OLD-1", "Altartikel", (), "Stk", Decimal("19"), active=False),
)
CATALOG = Catalog(ARTICLES)


@dataclass(frozen=True)
class Analysis:
    """Ergebnis aller Schritte für eine Mail."""

    result: ExtractionResult
    order: Order
    validation: ValidationResult

    def codes(self) -> set[str]:
        """Codes aller Befunde."""
        return {f.code for f in self.validation.findings}

    def error_codes(self) -> set[str]:
        """Codes der blockierenden Befunde."""
        return {f.code for f in self.validation.errors(self.order.acknowledged)}


def analyze(
    text: str,
    subject: str = "Bestellung",
    sender: str = "einkauf@kunde.de",
    *,
    html: bool = False,
    catalog: Catalog | None = CATALOG,
    profile: ImportProfile = PROFILE,
) -> Analysis:
    """Normalisiert, erkennt, ordnet zu und validiert eine Mail."""
    body = html_to_text(text) if html else text
    ctx = ParseContext(
        normalize(body, subject), subject, sender, "", MAIL_DATE, (profile.supplier,)
    )
    engine = ExtractionEngine(catalog.knows_name if catalog else None)
    result = engine.extract(ctx)
    matcher = ArticleMatcher(catalog) if catalog else None
    order = build_order("o1", "m1", result, sender_email=sender, profile=profile, matcher=matcher)
    return Analysis(result, order, validate_order(order, ValidationContext(today=TODAY)))


def context(text: str, subject: str = "", sender: str = "einkauf@kunde.de") -> ParseContext:
    """Parserkontext für Einzeltests einzelner Parser."""
    return ParseContext(normalize(text, subject), subject, sender, "", MAIL_DATE, (SUPPLIER,))


CERTAIN = Evidence("test", "Test", Confidence.CERTAIN)
INVOICE_ADDRESS = Address(
    company="Muster Werbetechnik GmbH",
    name="Thomas Müller",
    street="Musterweg",
    house_number="12a",
    postal_code="50667",
    city="Köln",
    country="DE",
    phone="0221 123456",
)
DELIVERY_ADDRESS = Address(
    company="Muster Werbetechnik GmbH",
    department="Lager",
    street="Industriestr.",
    house_number="5-7",
    postal_code="51570",
    city="Windeck",
    country="DE",
)


def matched_line(
    position: int, article: Article, quantity: str, price: str | None = None
) -> OrderLine:
    """Bestätigte, zugeordnete Position."""
    unit_price: Field[Decimal] = Field.found(Decimal(price), CERTAIN) if price else Field()
    return OrderLine(
        position=position,
        description=article.name,
        quantity=Field.found(Decimal(quantity), CERTAIN),
        unit_price=unit_price,
        match=ArticleMatch(MatchStatus.MATCHED, article, MatchStrategy.EXPLICIT_NUMBER, "Test"),
    )


def approved_order(**changes: object) -> Order:
    """Vollständiger, freigegebener Auftrag mit abweichender Lieferanschrift."""
    base = Order(
        id="order-0001",
        status=OrderStatus.APPROVED,
        document_number="AI-2026-000123",
        customer_reference=Field.found("PO-4711", CERTAIN),
        invoice_address=Field.found(INVOICE_ADDRESS, CERTAIN),
        delivery_address=Field.found(DELIVERY_ADDRESS, CERTAIN),
        delivery_same_as_invoice=False,
        contact=Field.found(Contact("Herr", "Thomas", "Müller", "t.mueller@muster.de"), CERTAIN),
        order_date=Field.found(date(2026, 9, 29), CERTAIN),
        payment_method=Field.found(PaymentMethod.INVOICE, CERTAIN),
        shipping_method=Field.found("UPS", CERTAIN),
        shipping_fee=Field.found(Decimal("8.90"), CERTAIN),
        lines=(
            matched_line(1, PRICED_ARTICLES[0], "3", "10.00"),
            matched_line(2, PRICED_ARTICLES[1], "1"),
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


PRICED_ARTICLES = (
    Article("YT11YBOR01", "YelloBlade Orange", (), "Stk", Decimal("19"), Decimal("10.00")),
    Article("YT15FILZ02", "Filzstreifen", (), "Stk", Decimal("19"), Decimal("1.18")),
    Article("BUCH-01", "Fachbuch", (), "Stk", Decimal("7"), Decimal("24.90")),
)
EXPORT_PROFILE = ImportProfile(
    id="standard",
    name="Standard",
    supplier=Address(
        company="Yellotools GmbH",
        street="Wilberhofener Str.",
        house_number="3",
        postal_code="51570",
        city="Windeck",
        country="DE",
    ),
)
