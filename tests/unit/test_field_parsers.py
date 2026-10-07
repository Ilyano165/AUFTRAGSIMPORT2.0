from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from icware_auftragsimport.domain.models import PaymentMethod
from icware_auftragsimport.domain.provenance import Confidence, FieldState
from icware_auftragsimport.parsers.common import Consumed, split_person_name
from icware_auftragsimport.parsers.contact import ContactParser
from icware_auftragsimport.parsers.customer import CustomerParser
from icware_auftragsimport.parsers.dates import DateParser, parse_date_text
from icware_auftragsimport.parsers.order_number import OrderNumberParser
from icware_auftragsimport.parsers.payment import PaymentParser, is_valid_iban
from icware_auftragsimport.parsers.shipping import ShippingParser
from support import MAIL_DATE, context


def _order_number(text: str, subject: str = "") -> tuple[str | None, FieldState, str]:
    field = OrderNumberParser().parse(context(text, subject), Consumed()).field
    method = field.evidence.method if field.evidence else ""
    return field.value, field.state, method


@pytest.mark.parametrize(
    "line",
    [
        "Bestellnummer: PO-251843",
        "Bestell-Nr. PO-251843",
        "Ihre Bestellnummer lautet PO-251843",
        "PO: PO-251843",
        "Auftragsnummer #PO-251843",
        "- Order No. PO-251843",
    ],
)
def test_labeled_order_numbers(line: str) -> None:
    assert _order_number(f"Hallo\n{line}\nDanke") == ("PO-251843", FieldState.RECOGNIZED, "labeled")


def test_subject_is_used_only_without_labeled_number() -> None:
    assert _order_number("5 x Rakel", "AW: Bestellung 2026-0715") == (
        "2026-0715",
        FieldState.RECOGNIZED,
        "subject",
    )


def test_conflicting_order_numbers_need_review() -> None:
    value, state, method = _order_number("Bestellnummer: 111\nBestellnummer: 222")
    assert (value, state, method) == ("111", FieldState.NEEDS_REVIEW, "conflict")


@pytest.mark.parametrize(
    ("text", "subject"),
    [
        ("Position 1: 5 x Rakel", ""),
        ("Bestellung vom 29.09.2026", "Bestellung vom 29.09."),
        ("Positionen folgen", "Bestellung"),
    ],
)
def test_no_false_order_numbers(text: str, subject: str) -> None:
    assert _order_number(text, subject)[1] is FieldState.UNKNOWN


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("29.09.2026", date(2026, 9, 29)),
        ("1.10.26", date(2026, 10, 1)),
        ("2026-10-05", date(2026, 10, 5)),
        ("7. Oktober 2026", date(2026, 10, 7)),
        ("7. Okt. 2026", date(2026, 10, 7)),
        ("31.02.2026", None),
        ("KW 41", None),
    ],
)
def test_date_formats(raw: str, expected: date | None) -> None:
    assert parse_date_text(raw) == expected


def test_labeled_dates_and_week_needs_review() -> None:
    result = DateParser().parse(
        context("Bestelldatum: 28.09.2026\nLiefertermin: KW 41"), Consumed()
    )
    assert result.order_date.value == date(2026, 9, 28)
    assert result.order_date.state is FieldState.RECOGNIZED
    assert result.requested_delivery.state is FieldState.NEEDS_REVIEW
    assert "Kalenderwoche 41" in result.notes[0].message


def test_mail_date_is_fallback_for_order_date() -> None:
    result = DateParser().parse(context("5 x Rakel"), Consumed())
    assert result.order_date.value == MAIL_DATE
    assert result.order_date.evidence is not None
    assert result.order_date.evidence.method == "mail_date_header"
    assert result.requested_delivery.state is FieldState.UNKNOWN


def test_customer_number_and_vat_ids() -> None:
    result = CustomerParser().parse(
        context("Kd.-Nr.: 10042\n5 x Rakel\n-- \nUSt-IdNr.: DE 123 456 789"), Consumed()
    )
    assert result.customer_number.value == "10042"
    assert result.vat_id.value == "DE123456789"
    assert result.vat_id.state is FieldState.RECOGNIZED


def test_unlabeled_vat_is_likely_and_invalid_vat_needs_review() -> None:
    unlabeled = CustomerParser().parse(context("Muster GmbH DE123456789"), Consumed()).vat_id
    invalid = CustomerParser().parse(context("USt-IdNr.: DE12345"), Consumed()).vat_id
    assert unlabeled.evidence is not None and unlabeled.evidence.confidence is Confidence.LIKELY
    assert invalid.state is FieldState.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("text", "method", "state"),
    [
        ("Zahlungsart: Rechnung", PaymentMethod.INVOICE, FieldState.RECOGNIZED),
        ("Zahlung: per Nachnahme", PaymentMethod.CASH_ON_DELIVERY, FieldState.RECOGNIZED),
        ("Zahlart: Vorkasse", PaymentMethod.PREPAYMENT, FieldState.RECOGNIZED),
        ("Zahlungsweise: SEPA-Lastschrift", PaymentMethod.DIRECT_DEBIT, FieldState.RECOGNIZED),
        ("Zahlungsbedingung: 14 Tage netto", PaymentMethod.INVOICE, FieldState.RECOGNIZED),
        ("Bitte liefern Sie auf Rechnung.", PaymentMethod.INVOICE, FieldState.NEEDS_REVIEW),
        (
            "Zahlungsart: Rechnung\nNa gut, dann per Nachnahme",
            PaymentMethod.INVOICE,
            FieldState.NEEDS_REVIEW,
        ),
        ("Zahlungsart: Paypal", None, FieldState.NEEDS_REVIEW),
        ("Der Artikel ist verfügbar.", None, FieldState.UNKNOWN),
        ("Überweisung der letzten Rechnung ist raus", None, FieldState.UNKNOWN),
    ],
)
def test_payment_methods(text: str, method: PaymentMethod | None, state: FieldState) -> None:
    field = PaymentParser().parse(context(text), Consumed()).method
    assert (field.value, field.state) == (method, state)


def test_payment_terms_in_signature_are_ignored() -> None:
    text = "5 x Rakel\n-- \nZahlungsbedingungen: Vorkasse"
    assert PaymentParser().parse(context(text), Consumed()).method.state is FieldState.UNKNOWN


def test_iban_checksum() -> None:
    assert is_valid_iban("DE89 3704 0044 0532 0130 00")
    assert not is_valid_iban("DE89 3704 0044 0532 0130 01")
    valid = PaymentParser().parse(context("IBAN: DE89 3704 0044 0532 0130 00"), Consumed()).iban
    invalid = PaymentParser().parse(context("IBAN: DE89370400440532013001"), Consumed()).iban
    assert (valid.value, valid.state) == ("DE89370400440532013000", FieldState.RECOGNIZED)
    assert invalid.state is FieldState.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("text", "value", "state"),
    [
        ("Versandart: DHL Express", "DHL Express", FieldState.RECOGNIZED),
        ("Versand: UPS Standard", "UPS", FieldState.RECOGNIZED),
        ("Lieferart: Selbstabholung", "Abholung", FieldState.RECOGNIZED),
        ("Versand: am 14.06.", None, FieldState.NEEDS_REVIEW),
        ("Bitte per GLS schicken", "GLS", FieldState.NEEDS_REVIEW),
        ("Versand am 14.06. bitte", None, FieldState.UNKNOWN),
    ],
)
def test_shipping_methods(text: str, value: str | None, state: FieldState) -> None:
    field = ShippingParser().parse(context(text), Consumed()).method
    assert (field.value, field.state) == (value, state)


def test_shipping_fee() -> None:
    clear = ShippingParser().parse(context("Versandkosten: 8,90 €"), Consumed()).fee
    ambiguous = ShippingParser().parse(context("Porto 8.90"), Consumed()).fee
    assert (clear.value, clear.state) == (Decimal("8.90"), FieldState.RECOGNIZED)
    assert ambiguous.state is FieldState.NEEDS_REVIEW


def test_contact_from_greeting_and_labels() -> None:
    text = "5 x Rakel\nTel.: 0171 1234567\n\nMit freundlichen Grüßen\n\nThomas Müller"
    field = ContactParser().parse(context(text), Consumed()).field
    assert field.value is not None
    assert (field.value.first_name, field.value.last_name) == ("Thomas", "Müller")
    assert field.value.phone == "0171 1234567"
    assert field.value.email == "einkauf@kunde.de"


def test_contact_label_with_salutation() -> None:
    field = ContactParser().parse(context("Ansprechpartner: Frau Anna von Berg"), Consumed()).field
    assert field.value is not None
    assert (field.value.salutation, field.value.first_name, field.value.last_name) == (
        "Frau",
        "Anna",
        "von Berg",
    )


def test_contact_email_of_forwarded_mail_is_original_sender() -> None:
    mail = "FYI\nAnfang der weitergeleiteten Nachricht:\n> Von: Kunde <k@kunde.de>\n> 5 x Rakel"
    field = ContactParser().parse(context(mail, "WG: x", "kollege@lieferant.de"), Consumed()).field
    assert field.value is not None and field.value.email == "k@kunde.de"
    assert field.evidence is not None and "weitergeleiteten" in field.evidence.reason


@pytest.mark.parametrize(
    ("name", "first", "last", "confidence"),
    [
        ("Thomas Müller", "Thomas", "Müller", Confidence.LIKELY),
        ("Anna von Berg", "Anna", "von Berg", Confidence.LIKELY),
        ("Hans Peter Schmidt", "Hans", "Peter Schmidt", Confidence.UNCERTAIN),
        ("Müller", "", "Müller", Confidence.UNCERTAIN),
    ],
)
def test_person_name_split(name: str, first: str, last: str, confidence: Confidence) -> None:
    assert split_person_name(name) == (first, last, confidence)
