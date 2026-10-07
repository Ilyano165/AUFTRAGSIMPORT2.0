"""Spezifikation, Adapter, Serializer und Validator des Lexware-Exports."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pytest

from icware_auftragsimport.domain.findings import Severity
from icware_auftragsimport.domain.models import Address, MatchStatus, PaymentMethod
from icware_auftragsimport.domain.provenance import Confidence, Evidence, Field
from icware_auftragsimport.export.lexware.adapter import LexwareExportAdapter
from icware_auftragsimport.export.lexware.serializer import (
    format_money,
    format_quantity,
    format_tax,
)
from icware_auftragsimport.export.lexware.spec import load_spec
from icware_auftragsimport.export.model import PreparedExport
from icware_auftragsimport.export.validator import ExportValidator, check_file_name
from support import (
    CERTAIN,
    DELIVERY_ADDRESS,
    EXPORT_PROFILE,
    INVOICE_ADDRESS,
    PRICED_ARTICLES,
    approved_order,
    matched_line,
)

REFERENCE = (
    Path(__file__).resolve().parents[2] / "integration" / "lexware" / "reference" / "248090.xml"
)
NOW = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
NS = {"o": "http://www.opentrans.org/XMLSchema/1.0"}
ADAPTER = LexwareExportAdapter()
VALIDATOR = ExportValidator(ADAPTER.spec)


def _prepare(**changes: object) -> PreparedExport:
    return ADAPTER.prepare(
        approved_order(**changes), EXPORT_PROFILE, now=NOW, number="AI-2026-000123"
    )


def _codes(prepared: PreparedExport) -> dict[str, Severity]:
    return {f.code: f.severity for f in prepared.findings}


def _render(prepared: PreparedExport, encoding: str = "UTF-8") -> bytes:
    assert prepared.document is not None
    return ADAPTER.render(prepared.document, encoding)


def test_spec_is_complete_and_every_element_has_a_source() -> None:
    spec = load_spec()
    assert spec.status == "Exportadapter implementiert – Zielsystemvalidierung ausstehend"
    assert len(spec.open_questions) == 21
    stack = [spec.tree]
    while stack:
        node = stack.pop()
        assert node.basis, node.tag
        stack.extend(node.children)


def test_reference_file_matches_spec_except_documented_deviation() -> None:
    findings = VALIDATOR.check_xml(REFERENCE.read_bytes(), "ISO-8859-1")
    prefix = "ORDER_LIST/ORDER/ORDER_ITEM_LIST/ORDER_ITEM[1]/ARTICLE_PRICE"
    assert [f.message for f in findings] == [
        f"{prefix}/PRICE_AMOUNT: Falsche Reihenfolge: steht nach TAX",
        f"{prefix}/PRICE_LINE_AMOUNT: Falsche Reihenfolge: steht nach TAX",
    ]


def test_rendered_file_passes_all_checks() -> None:
    prepared = _prepare()
    xml = _render(prepared)
    result = VALIDATOR.validate(prepared, xml=xml, file_name="AI-2026-000123.xml", encoding="UTF-8")
    assert result.errors() == ()
    assert xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\r\n<ORDER_LIST>\r\n')
    assert b"<![CDATA[PO-4711]]>" in xml


def test_mapping_follows_lexware_semantics() -> None:
    root = ElementTree.fromstring(_render(_prepare()))
    buyer = root.find(".//o:BUYER_PARTY/o:PARTY/o:ADDRESS", NS)
    invoice = root.find(".//o:INVOICE_PARTY/o:PARTY/o:ADDRESS", NS)
    assert buyer is not None and invoice is not None
    assert buyer.findtext("o:STREET", namespaces=NS) == "Industriestr. 5-7"
    assert invoice.findtext("o:STREET", namespaces=NS) == "Musterweg 12a"
    assert [
        invoice.findtext(f"o:{t}", namespaces=NS) for t in ("NAME", "NAME2", "NAME3", "COUNTRY")
    ] == [
        "Muster Werbetechnik GmbH",
        "Müller",
        "Thomas",
        "Deutschland",
    ]
    assert root.findtext(".//o:PAYMENT/o:CASH/o:PAYMENT_TERM", namespaces=NS) == "10"
    first = root.find(".//o:ORDER_ITEM", NS)
    assert first is not None
    price = "o:ARTICLE_PRICE/o:"
    paths = ("o:QUANTITY", f"{price}PRICE_AMOUNT", f"{price}PRICE_LINE_AMOUNT", f"{price}TAX")
    assert [first.findtext(p, namespaces=NS) for p in paths] == ["3", "10.00", "30.00", "0.19"]
    assert root.findtext(".//o:REMARK[@type='shipping_fee']", namespaces=NS) == "8.90"
    assert root.findtext(".//o:ORDER_DATE", namespaces=NS) == "2026-09-29 00:00:00"


def test_same_delivery_address_repeats_invoice_party_like_reference() -> None:
    root = ElementTree.fromstring(_render(_prepare(delivery_same_as_invoice=True)))
    streets = [e.text for e in root.iterfind(".//o:ORDER_PARTIES/*/o:PARTY/o:ADDRESS/o:STREET", NS)]
    assert streets == ["Musterweg 12a", "Musterweg 12a", "Wilberhofener Str. 3"]


@pytest.mark.parametrize(
    ("method", "container", "code"),
    [
        (PaymentMethod.INVOICE, "CASH", "10"),
        (PaymentMethod.PREPAYMENT, "CASH", "25"),
        (PaymentMethod.CASH_ON_DELIVERY, "CASH", "52"),
        (PaymentMethod.CASH, "CASH", "56"),
        (PaymentMethod.DIRECT_DEBIT, "ACCOUNT", "54"),
    ],
)
def test_payment_codes(method: PaymentMethod, container: str, code: str) -> None:
    prepared = _prepare(payment_method=Field.found(method, CERTAIN))
    root = ElementTree.fromstring(_render(prepared))
    assert root.findtext(f".//o:PAYMENT/o:{container}/o:PAYMENT_TERM", namespaces=NS) == code
    if method is PaymentMethod.DIRECT_DEBIT:
        assert root.findtext(".//o:ACCOUNT/o:BANK_ACCOUNT", namespaces=NS) == ""
        assert "EXP_NO_BANK_DATA" in _codes(prepared)


UNCERTAIN = Evidence("t", "unsicher", Confidence.UNCERTAIN)
_LINE = matched_line(1, PRICED_ARTICLES[0], "1")
UNRESOLVED_LINE = replace(_LINE, match=replace(_LINE.match, status=MatchStatus.NEEDS_REVIEW))
AUSTRIAN_DELIVERY = replace(DELIVERY_ADDRESS, postal_code="6020", city="Innsbruck", country="AT")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {
                "lines": (
                    replace(
                        matched_line(1, PRICED_ARTICLES[0], "1"),
                        match=replace(
                            matched_line(1, PRICED_ARTICLES[0], "1").match,
                            status=__import__(
                                "icware_auftragsimport.domain.models", fromlist=["MatchStatus"]
                            ).MatchStatus.NEEDS_REVIEW,
                        ),
                    ),
                )
            },
            "EXP_ARTICLE_UNRESOLVED",
        ),
        (
            {"lines": (matched_line(1, replace(PRICED_ARTICLES[0], price=None), "1"),)},
            "EXP_PRICE_MISSING",
        ),
        (
            {"lines": (matched_line(1, replace(PRICED_ARTICLES[0], tax_rate=None), "1"),)},
            "EXP_TAX_MISSING",
        ),
        ({"lines": ()}, "EXP_NO_LINES"),
        (
            {"payment_method": Field.review(PaymentMethod.INVOICE, UNCERTAIN)},
            "EXP_PAYMENT_UNCONFIRMED",
        ),
        ({"payment_method": Field()}, "EXP_PAYMENT_MISSING"),
        (
            {"invoice_address": Field.review(Address(company="X"), UNCERTAIN)},
            "EXP_INVOICE_UNCONFIRMED",
        ),
        (
            {"invoice_address": Field.found(Address(company="Nur Firma"), CERTAIN)},
            "EXP_ADDRESS_INCOMPLETE",
        ),
        (
            {
                "delivery_address": Field.found(
                    replace(
                        __import__("support").DELIVERY_ADDRESS,
                        postal_code="6020",
                        city="Innsbruck",
                        country="AT",
                    ),
                    CERTAIN,
                )
            },
            "EXP_FOREIGN_CUSTOMER_UNVERIFIED",
        ),
        ({"order_date": Field()}, "EXP_ORDER_DATE_MISSING"),
    ],
)
def test_blocking_findings(changes: dict[str, object], code: str) -> None:
    prepared = _prepare(**changes)
    assert prepared.document is None
    assert _codes(prepared)[code] is Severity.ERROR


def test_missing_supplier_country_blocks() -> None:
    profile = replace(EXPORT_PROFILE, supplier=Address(company="Yellotools GmbH"))
    prepared = ADAPTER.prepare(approved_order(), profile, now=NOW, number="AI-1")
    assert "EXP_SUPPLIER_COUNTRY_MISSING" in _codes(prepared) and prepared.document is None


def test_non_blocking_findings_are_reported() -> None:
    lines = (
        matched_line(1, PRICED_ARTICLES[0], "3", "12.00"),
        matched_line(2, PRICED_ARTICLES[1], "1"),
    )
    invoice = replace(INVOICE_ADDRESS, name="Hans Peter Schmidt")
    prepared = _prepare(
        lines=lines,
        invoice_address=Field.found(invoice, CERTAIN),
        shipping_method=Field.found("United Parcel Service Standard (Express Saver)", CERTAIN),
        customer_reference=Field(),
    )
    codes = _codes(prepared)
    assert prepared.document is not None
    assert codes["EXP_PRICE_DIFFERS"] is Severity.WARNING
    assert codes["EXP_PRICE_FROM_CATALOG"] is Severity.INFO
    assert codes["EXP_DEPARTMENT_DROPPED"] is Severity.WARNING
    assert codes["EXP_NAME_NOT_SPLIT"] is Severity.WARNING
    assert codes["EXP_DELIVERY_METHOD_LONG"] is Severity.WARNING
    assert codes["EXP_ORDER_ID_IS_DOCUMENT_NUMBER"] is Severity.INFO
    assert prepared.document.external_order_id == "AI-2026-000123"
    assert prepared.document.invoice_party.last_name == "Hans Peter Schmidt"


def test_euro_sign_is_replaced_and_recorded() -> None:
    prepared = _prepare(note="Rabatt 5 € abziehen")
    assert prepared.document is not None and prepared.document.remark == "Rabatt 5 EUR abziehen"
    assert any("€" in t for t in prepared.transformations)


def test_encoding_problems_are_reported_not_replaced() -> None:
    city = replace(INVOICE_ADDRESS, city="Łódź")
    prepared = _prepare(invoice_address=Field.found(city, CERTAIN))
    iso = VALIDATOR.validate(prepared, xml=None, file_name=None, encoding="ISO-8859-1")
    utf = VALIDATOR.validate(prepared, xml=_render(prepared), file_name=None, encoding="UTF-8")
    assert [f.code for f in iso.errors()] == ["EXP_UNENCODABLE"]
    assert utf.errors() == ()


@pytest.mark.parametrize("notation", ["cdata", "entities"])
def test_special_characters_survive_both_notations(notation: str) -> None:
    text = "A & B <C> \"D\" 'E' ]]> F"
    prepared = _prepare(note=text)
    assert prepared.document is not None
    xml = ADAPTER.render(prepared.document, "UTF-8", notation=notation)
    root = ElementTree.fromstring(xml)
    assert root.findtext(".//o:REMARK[@type='order']", namespaces=NS) == text


def test_umlauts_in_iso_8859_1() -> None:
    prepared = _prepare()
    xml = _render(prepared, "ISO-8859-1")
    assert "Müller".encode("iso-8859-1") in xml
    assert (
        VALIDATOR.validate(prepared, xml=xml, file_name=None, encoding="ISO-8859-1").errors() == ()
    )


def test_validator_detects_tampering_and_foreign_structures() -> None:
    prepared = _prepare()
    xml = _render(prepared)
    tampered = xml.replace(b"<QUANTITY><![CDATA[3]]>", b"<QUANTITY><![CDATA[4]]>")
    extra = xml.replace(b"<ORDER_UNIT>", b"<COLOR><![CDATA[rot]]></COLOR><ORDER_UNIT>", 1)
    doctype = xml.replace(b"<ORDER_LIST>", b"<!DOCTYPE x><ORDER_LIST>")
    wrong_encoding = xml.replace(b'encoding="UTF-8"', b'encoding="ISO-8859-1"')

    def codes(data: bytes) -> set[str]:
        return {
            f.code
            for f in VALIDATOR.validate(
                prepared, xml=data, file_name=None, encoding="UTF-8"
            ).errors()
        }

    assert "EXP_ROUNDTRIP" in codes(tampered)
    assert "EXP_STRUCTURE" in codes(extra)
    assert codes(doctype) == {"EXP_XML_DOCTYPE"}
    assert "EXP_XML_ENCODING" in codes(wrong_encoding)


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("AI-2026-000123.xml", True),
        ("TEST_AI-2026-000123.xml", True),
        ("CON.xml", False),
        ("../AI.xml", False),
        ("AI 1.xml", False),
        ("AI.XML.exe", False),
        (f"{'A' * 120}.xml", False),
    ],
)
def test_file_names(name: str, ok: bool) -> None:
    assert (check_file_name(name) == []) is ok


@pytest.mark.parametrize(
    ("value", "money", "quantity"),
    [
        ("8.9", "8.90", "8.9"),
        ("10", "10.00", "10"),
        ("0.0450", "0.0450", "0.045"),
        ("2.500", "2.500", "2.5"),
    ],
)
def test_number_formats(value: str, money: str, quantity: str) -> None:
    assert format_money(Decimal(value)) == money
    assert format_quantity(Decimal(value)) == quantity


def test_tax_format() -> None:
    assert [format_tax(Decimal(x) / 100) for x in ("19", "7", "0", "5.5")] == [
        "0.19",
        "0.07",
        "0",
        "0.055",
    ]
