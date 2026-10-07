from __future__ import annotations

import pytest

from icware_auftragsimport.domain.models import Address
from icware_auftragsimport.domain.provenance import FieldState
from icware_auftragsimport.parsers.address import AddressParser, AddressResult
from icware_auftragsimport.parsers.common import Consumed
from support import context


def _parse(text: str) -> AddressResult:
    return AddressParser().parse(context(text), Consumed())


def test_invoice_and_delivery_blocks_are_separate_objects() -> None:
    result = _parse(
        "Rechnungsadresse:\nMuster Werbetechnik GmbH\nz. Hd. Herrn Thomas Müller\nMusterweg 12a\n"
        "50667 Köln\n\nLieferadresse:\nMuster GmbH Lager\nAbteilung Wareneingang\n"
        "Industriestr. 5-7\nA-6020 Innsbruck\nTel.: 0512 123456"
    )
    assert result.invoice.value == Address(
        company="Muster Werbetechnik GmbH",
        name="Thomas Müller",
        street="Musterweg",
        house_number="12a",
        postal_code="50667",
        city="Köln",
        country="DE",
    )
    assert result.invoice.state is FieldState.RECOGNIZED
    assert result.delivery.value == Address(
        company="Muster GmbH Lager",
        department="Abteilung Wareneingang",
        street="Industriestr.",
        house_number="5-7",
        postal_code="6020",
        city="Innsbruck",
        country="AT",
        phone="0512 123456",
    )
    assert not result.delivery_same_as_invoice


def test_sentence_with_rechnung_an_is_no_header() -> None:
    result = _parse("Bitte schicken Sie die Rechnung an:\nbuchhaltung@kunde.de\n5 x Rakel")
    assert result.invoice.state is FieldState.UNKNOWN


def test_four_digit_postcode_without_country_needs_review() -> None:
    result = _parse("Rechnungsadresse:\nBeispiel AG\nWeg 1\n1010 Wien")
    assert result.invoice.state is FieldState.NEEDS_REVIEW
    assert result.invoice.value is not None and result.invoice.value.country == ""
    assert result.invoice.evidence is not None
    assert "4-stellige PLZ" in result.invoice.evidence.reason


def test_explicit_country_line_resolves_four_digit_postcode() -> None:
    result = _parse("Rechnungsadresse:\nBeispiel AG\nWeg 1\n8001 Zürich\nSchweiz")
    assert result.invoice.value is not None and result.invoice.value.country == "CH"
    assert result.invoice.state is FieldState.RECOGNIZED


def test_company_without_legal_form_needs_review() -> None:
    result = _parse("Rechnungsadresse:\nGetränke Schulz\nKarl Schulz\nHauptstr. 1\n53783 Eitorf")
    assert result.invoice.state is FieldState.NEEDS_REVIEW
    assert result.invoice.value is not None
    assert (result.invoice.value.company, result.invoice.value.name) == (
        "Getränke Schulz",
        "Karl Schulz",
    )


def test_inline_address_after_header() -> None:
    result = _parse("Lieferadresse: Beispiel GmbH, Hauptstr. 5, D-10115 Berlin")
    assert result.delivery.value is not None
    assert result.delivery.value.summary() == "Beispiel GmbH, 10115 Berlin"
    assert result.invoice.state is FieldState.NEEDS_REVIEW


def test_labeled_form_fields() -> None:
    result = _parse(
        "Rechnungsadresse:\nFirma: Beispiel GmbH\nStraße: Am Markt 3 b\nPLZ/Ort: 10115 Berlin\n"
        "Land: Deutschland"
    )
    assert result.invoice.value is not None
    assert (result.invoice.value.street, result.invoice.value.house_number) == ("Am Markt", "3b")
    assert result.invoice.value.country == "DE"


@pytest.mark.parametrize(
    "line",
    [
        "Lieferadresse wie Rechnungsadresse",
        "Lieferanschrift: identisch",
        "Lieferadresse = Rechnungsadresse",
        "Lieferung an die Rechnungsadresse",
    ],
)
def test_delivery_same_as_invoice(line: str) -> None:
    result = _parse(f"{line}\n\nRechnungsadresse:\nBau KG\nBahnhofstr. 1\n12345 Berlin")
    assert result.delivery_same_as_invoice
    assert result.delivery.state is FieldState.RECOGNIZED


def test_invoice_same_as_delivery() -> None:
    result = _parse(
        "Lieferadresse: Beispiel AG, Weg 1, A-1010 Wien\nRechnungsadresse wie Lieferadresse"
    )
    assert result.invoice.state is FieldState.RECOGNIZED
    assert result.invoice.value is not None and result.invoice.value.country == "AT"
    assert result.delivery_same_as_invoice


def test_conflicting_delivery_statements_need_review() -> None:
    result = _parse(
        "Lieferadresse wie Rechnungsadresse\n\nLieferadresse:\nLager GmbH\nWeg 2\n50667 Köln"
    )
    assert result.delivery.state is FieldState.NEEDS_REVIEW
    assert not result.delivery_same_as_invoice


def test_signature_address_is_only_a_suggestion() -> None:
    result = _parse(
        "5 x Rakel\n\nMit freundlichen Grüßen\nThomas Müller\nMüller Getränke GmbH\n"
        "Hauptstraße 3\n44135 Dortmund"
    )
    assert result.invoice.state is FieldState.NEEDS_REVIEW
    assert result.invoice.value is not None
    assert (result.invoice.value.company, result.invoice.value.name) == (
        "Müller Getränke GmbH",
        "Thomas Müller",
    )


def test_own_supplier_address_is_ignored() -> None:
    result = _parse("Beispiel Großhandel GmbH\nLagerweg 4\n51570 Windeck")
    assert result.invoice.state is FieldState.UNKNOWN
    assert any(n.code == "OWN_ADDRESS_IGNORED" for n in result.notes)


def test_po_box_is_parsed() -> None:
    result = _parse("Rechnungsadresse:\nBeispiel GmbH\nPostfach 12 34\n50667 Köln")
    assert result.invoice.value is not None and result.invoice.value.is_po_box()
