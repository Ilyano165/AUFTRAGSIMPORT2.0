from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from icware_auftragsimport.domain.findings import Severity
from icware_auftragsimport.domain.models import (
    Address,
    ArticleMatch,
    MatchStatus,
    MatchStrategy,
    Order,
    OrderLine,
    PaymentMethod,
)
from icware_auftragsimport.domain.provenance import Confidence, Evidence, Field
from icware_auftragsimport.domain.status import OrderStatus
from icware_auftragsimport.infrastructure.repositories import OrderRef
from icware_auftragsimport.services.validation import (
    ValidationContext,
    status_after_validation,
    validate_order,
)
from support import ARTICLES, TODAY

SURE = Evidence("test", "Test", Confidence.CERTAIN)
ADDRESS = Address(
    company="Kunde GmbH",
    street="Weg",
    house_number="1",
    postal_code="50667",
    city="Köln",
    country="DE",
)


def _line(
    position: int = 1, quantity: str = "5", article_index: int = 0, unit: str = ""
) -> OrderLine:
    return OrderLine(
        position,
        ARTICLES[article_index].name,
        Field.found(Decimal(quantity), SURE),
        unit=unit,
        match=ArticleMatch(
            MatchStatus.MATCHED, ARTICLES[article_index], MatchStrategy.NAME, "Test"
        ),
    )


def _order(**changes: object) -> Order:
    base = Order(
        id="o1",
        customer_reference=Field.found("PO-1", SURE),
        invoice_address=Field.found(ADDRESS, SURE),
        delivery_same_as_invoice=True,
        order_date=Field.found(date(2026, 9, 29), SURE),
        payment_method=Field.found(PaymentMethod.INVOICE, SURE),
        lines=(_line(),),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _codes(order: Order, ctx: ValidationContext | None = None) -> dict[str, Severity]:
    result = validate_order(order, ctx or ValidationContext(today=TODAY))
    return {f.code: f.severity for f in result.findings}


def test_complete_order_is_ready() -> None:
    result = validate_order(_order(), ValidationContext(today=TODAY))
    assert result.findings == ()
    assert status_after_validation(result, frozenset()) is OrderStatus.READY


def test_missing_lines_payment_and_address_block_export() -> None:
    codes = _codes(
        _order(lines=(), payment_method=Field.unknown(), invoice_address=Field.unknown())
    )
    assert codes["LINES_MISSING"] is Severity.ERROR
    assert codes["PAYMENT_METHOD_MISSING"] is Severity.ERROR
    assert codes["INVOICE_ADDRESS_MISSING"] is Severity.ERROR


def test_company_only_address_is_incomplete() -> None:
    codes = _codes(_order(invoice_address=Field.found(Address(company="Nur Firma"), SURE)))
    assert codes["ADDRESS_INCOMPLETE"] is Severity.ERROR


def test_review_states_block_until_confirmed() -> None:
    review = Field.review(PaymentMethod.INVOICE, Evidence("x", "Fließtext", Confidence.UNCERTAIN))
    order = _order(payment_method=review)
    assert _codes(order)["PAYMENT_METHOD_REVIEW"] is Severity.ERROR
    confirmed = replace(order, payment_method=Field.manual(PaymentMethod.INVOICE))
    assert "PAYMENT_METHOD_REVIEW" not in _codes(confirmed)


def test_line_problems() -> None:
    unknown = replace(_line(2), match=ArticleMatch(MatchStatus.UNKNOWN, reason="Kein Treffer"))
    review = replace(_line(3), match=ArticleMatch(MatchStatus.NEEDS_REVIEW, reason="ähnlich"))
    zero = _line(4, "0")
    precise = _line(5, "1.2345")
    mismatch = _line(6, unit="Karton")
    codes = _codes(_order(lines=(_line(), unknown, review, zero, precise, mismatch, _line(7))))
    assert codes["LINE_ARTICLE_UNKNOWN"] is Severity.ERROR
    assert codes["LINE_ARTICLE_REVIEW"] is Severity.ERROR
    assert codes["LINE_QUANTITY_INVALID"] is Severity.ERROR
    assert codes["LINE_QUANTITY_PRECISION"] is Severity.ERROR
    assert codes["LINE_UNIT_MISMATCH"] is Severity.WARNING
    assert codes["LINE_DUPLICATE_ARTICLE"] is Severity.WARNING


def test_delivery_address_checks() -> None:
    po_box = replace(ADDRESS, street="Postfach", house_number="1234")
    codes = _codes(
        _order(delivery_same_as_invoice=False, delivery_address=Field.found(po_box, SURE))
    )
    assert codes["DELIVERY_PO_BOX"] is Severity.WARNING
    missing = _codes(_order(delivery_same_as_invoice=False))
    assert missing["DELIVERY_ADDRESS_MISSING"] is Severity.ERROR


def test_postal_and_country_checks() -> None:
    wrong = replace(ADDRESS, postal_code="5066")
    unchecked = replace(ADDRESS, country="SE", postal_code="11122")
    assert (
        _codes(_order(invoice_address=Field.found(wrong, SURE)))["ADDRESS_POSTAL_FORMAT"]
        is Severity.ERROR
    )
    assert (
        _codes(_order(invoice_address=Field.found(unchecked, SURE)))["ADDRESS_POSTAL_UNCHECKED"]
        is Severity.INFO
    )


def test_optional_fields_only_warn() -> None:
    codes = _codes(
        _order(
            customer_reference=Field.unknown(),
            shipping_method=Field.review(None, Evidence("x", "unklar", Confidence.UNCERTAIN)),
            requested_delivery=Field.found(date(2026, 9, 1), SURE),
        )
    )
    assert codes == {
        "CUSTOMER_REFERENCE_MISSING": Severity.WARNING,
        "SHIPPING_METHOD_REVIEW": Severity.WARNING,
        "REQUESTED_DELIVERY_PAST": Severity.WARNING,
    }


def test_duplicates_can_be_acknowledged_consciously() -> None:
    ctx = ValidationContext(
        today=TODAY,
        reference_conflicts=(OrderRef("o0", OrderStatus.EXPORTED, "AI-2026-000001", 3),),
        duplicate_mail_of="m0",
    )
    result = validate_order(_order(), ctx)
    keys = frozenset(f.key for f in result.findings)
    assert not result.is_exportable()
    assert result.is_exportable(keys)
    assert "AI-2026-000001" in result.findings[0].message


def test_ignored_conflicts_do_not_count() -> None:
    ctx = ValidationContext(
        today=TODAY, reference_conflicts=(OrderRef("o0", OrderStatus.IGNORED, None, 1),)
    )
    assert validate_order(_order(), ctx).findings == ()
