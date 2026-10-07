"""Integrationstest-Paket für eine echte Windows-/Lexware-Testinstallation.

Erzeugt für jeden Testfall die Eingabe, die erwartete XML-Datei (vom Produkt erzeugt) und
gezielte Diagnosedateien. Diagnosedateien sind ausdrücklich keine Produktausgaben: Sie
klären offene Fragen (OQ) zum Lexware-Verhalten.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from ...config.schema import ImportProfile
from ...domain.findings import Severity
from ...domain.models import (
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
from ...domain.provenance import Confidence, Evidence, Field
from ...domain.status import OrderStatus
from ..validator import ExportValidator
from .adapter import LexwareExportAdapter

GENERATED_AT = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
LARGE_ORDER_LINES = 120
_SURE = Evidence("integrationstest", "Testfall", Confidence.CERTAIN)
TEST_ARTICLES = (
    Article("TEST-ART-1", "Testartikel A", (), "Stk", Decimal(19), Decimal("10.00")),
    Article("TEST-ART-2", "Testartikel B", (), "Stk", Decimal(19), Decimal("5.50")),
    Article("TEST-ART-3", "Testartikel C ermäßigt", (), "Stk", Decimal(7), Decimal("24.90")),
)
UNKNOWN_ARTICLE = "GIBT-ES-NICHT-99"
CUSTOMER = Address(
    company="Testkunde Lexware GmbH",
    name="Erika Mustermann",
    street="Teststraße",
    house_number="1",
    postal_code="50667",
    city="Köln",
    country="DE",
    phone="0221 000000",
    email="einkauf@testkunde.invalid",
)
DELIVERY = Address(
    company="Testkunde Lexware GmbH",
    name="Max Lager",
    street="Lagerweg",
    house_number="7b",
    postal_code="53783",
    city="Eitorf",
    country="DE",
)


@dataclass(frozen=True, slots=True)
class IntegrationCase:
    """Ein Testfall des Integrationstestplans."""

    number: str
    title: str
    purpose: str
    procedure: str
    lexware_expectation: str
    open_questions: tuple[str, ...]
    order: Order
    encoding: str = "UTF-8"
    notation: str | None = None
    diagnostics: tuple[tuple[str, str, Callable[[str], str]], ...] = ()


def _line(position: int, article: Article, quantity: str, price: str | None = None) -> OrderLine:
    resolved = article.number != UNKNOWN_ARTICLE
    return OrderLine(
        position=position,
        description=article.name,
        quantity=Field.found(Decimal(quantity), _SURE),
        unit_price=Field.found(Decimal(price), _SURE) if price else Field(),
        match=ArticleMatch(
            MatchStatus.MATCHED if resolved else MatchStatus.UNKNOWN,
            article if resolved else None,
            MatchStrategy.EXPLICIT_NUMBER,
            "Testfall",
        ),
    )


def _order(number: str, **changes: object) -> Order:
    base = Order(
        id=f"integrationstest-{number}",
        status=OrderStatus.APPROVED,
        document_number=f"IT-2026-{number.zfill(6)}",
        customer_reference=Field.found(f"IT-{number}", _SURE),
        invoice_address=Field.found(CUSTOMER, _SURE),
        delivery_same_as_invoice=True,
        contact=Field.found(Contact("Frau", "Erika", "Mustermann", CUSTOMER.email), _SURE),
        order_date=Field.found(date(2026, 10, 1), _SURE),
        payment_method=Field.found(PaymentMethod.INVOICE, _SURE),
        lines=(_line(1, TEST_ARTICLES[0], "1"),),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _without_price(xml: str) -> str:
    start, end = (
        xml.index("        <ARTICLE_PRICE"),
        xml.index("</ARTICLE_PRICE>") + len("</ARTICLE_PRICE>\r\n"),
    )
    return xml[:start] + xml[end:]


def _zero_price(xml: str) -> str:
    return xml.replace("<![CDATA[10.00]]>", "<![CDATA[0.00]]>")


def _iso_date(xml: str) -> str:
    return xml.replace("<![CDATA[2026-10-01 00:00:00]]>", "<![CDATA[2026-10-01T00:00:00+02:00]]>")


def _unknown_article(xml: str) -> str:
    return xml.replace("<![CDATA[TEST-ART-1]]>", f"<![CDATA[{UNKNOWN_ARTICLE}]]>")


def build_cases() -> list[IntegrationCase]:
    """Alle Testfälle in fester Reihenfolge."""
    umlauts = replace(
        CUSTOMER,
        company="Bäckerei Größe & Söhne GmbH",
        name="Jürgen Öztürk",
        street="Äußere Ölmühlstraße",
        city="Mönchengladbach",
        postal_code="41061",
    )
    special = replace(CUSTOMER, company='Müller & Partner <Technik> "Nord" GmbH')
    long_remark = "Bitte vor Anlieferung anrufen. " * 10
    return [
        IntegrationCase(
            "01",
            "Einfache Bestellung",
            "Grundfunktion, Kundenanlage, Datumsformat",
            "Datei importieren, als Auftrag übernehmen, keinen Kunden zuordnen",
            "Auftrag mit 1 Position TEST-ART-1, Menge 1, 10,00 netto, 19 %; Bestellnummer IT-01; "
            "Auftragsdatum 01.10.2026; Zahlungsart Rechnung; neuer Kunde angelegt",
            ("OQ-01", "OQ-04", "OQ-08", "OQ-17"),
            _order("01", lines=(_line(1, TEST_ARTICLES[0], "1", "10.00"),)),
            diagnostics=(("01b-datum-iso.xml", "ORDER_DATE im ISO-Format (OQ-04)", _iso_date),),
        ),
        IntegrationCase(
            "02",
            "Mehrere Positionen",
            "Mengen > 1, Zeilensumme, gemischte Steuersätze",
            "Importieren und übernehmen",
            "3 Positionen: TEST-ART-1 3 × 10,00 = 30,00; TEST-ART-2 2 × 5,50 = 11,00; "
            "TEST-ART-3 1 × 24,90 (7 %). Entscheidend: Einzelpreis bleibt 10,00 (nicht 30,00)",
            ("OQ-05", "OQ-18"),
            _order(
                "02",
                lines=(
                    _line(1, TEST_ARTICLES[0], "3", "10.00"),
                    _line(2, TEST_ARTICLES[1], "2", "5.50"),
                    _line(3, TEST_ARTICLES[2], "1", "24.90"),
                ),
            ),
        ),
        IntegrationCase(
            "03",
            "Lieferadresse abweichend",
            "BUYER_PARTY als Lieferadresse",
            "Einmal ohne Kundenzuordnung, einmal einem bestehenden Kunden mit vorhandener "
            "Lieferadresse zuordnen",
            "Rechnungsadresse Köln, Lieferadresse Lagerweg 7b, 53783 Eitorf; bei bestehendem "
            "Kunden "
            "laut LX-SPEC nur Ergänzung leerer Felder – prüfen, welche Lieferadresse im Auftrag "
            "steht",
            ("OQ-07",),
            _order(
                "03", delivery_same_as_invoice=False, delivery_address=Field.found(DELIVERY, _SURE)
            ),
        ),
        IntegrationCase(
            "04",
            "Umlaute",
            "Zeichensatz UTF-8 (Empfehlung LX-SPEC)",
            "Importieren und übernehmen",
            "Firma, Name, Straße und Ort exakt mit ä, ö, ü, Ä, Ö, Ü, ß",
            ("OQ-02",),
            _order("04", invoice_address=Field.found(umlauts, _SURE)),
        ),
        IntegrationCase(
            "04b",
            "Umlaute ISO-8859-1",
            "Zeichensatz der Referenzdatei",
            "Wie Test 04",
            "Identisches Ergebnis wie Test 04",
            ("OQ-02",),
            _order("04b", invoice_address=Field.found(umlauts, _SURE)),
            encoding="ISO-8859-1",
        ),
        IntegrationCase(
            "05",
            "Sonderzeichen",
            "&, <, >, Anführungszeichen, €, lange Texte (CDATA)",
            "Importieren und übernehmen; Bemerkung und Versandart prüfen",
            "Firmenname exakt wie Eingabe; Bemerkung mit „EUR“ statt €; lange Bemerkung "
            "vollständig "
            "oder dokumentiert gekürzt; Versandart (40 Zeichen) vollständig oder gekürzt",
            ("OQ-03", "OQ-13"),
            _order(
                "05",
                invoice_address=Field.found(special, _SURE),
                note=f"Rabatt 5 € abziehen. {long_remark}",
                shipping_method=Field.found("Spedition Express Termin vor 10 Uhr", _SURE),
            ),
        ),
        IntegrationCase(
            "05b",
            "Sonderzeichen als Entitäten",
            "Notation laut LX-SPEC 3.1.3",
            "Wie Test 05",
            "Identisches Ergebnis wie Test 05",
            ("OQ-03",),
            _order(
                "05b",
                invoice_address=Field.found(special, _SURE),
                note=f"Rabatt 5 € abziehen. {long_remark}",
                shipping_method=Field.found("Spedition Express Termin vor 10 Uhr", _SURE),
            ),
            notation="entities",
        ),
        IntegrationCase(
            "06",
            "Fehlender Preis",
            "Produkt blockiert; Diagnosedateien klären das Lexware-Verhalten",
            "Nur die Diagnosedateien 06a/06b importieren",
            "Produkt: kein Export (EXP_PRICE_MISSING). 06a ohne ARTICLE_PRICE, 06b Preis 0,00: "
            "beobachten, ob Lexware den Stammpreis nimmt oder 0,00 als manuellen Preis setzt",
            ("OQ-06",),
            _order("06", lines=(_line(1, replace(TEST_ARTICLES[0], price=None), "1"),)),
            diagnostics=(
                ("06a-ohne-preis.xml", "ARTICLE_PRICE entfernt (OQ-06)", _without_price),
                ("06b-preis-null.xml", "Preis 0,00 (OQ-06)", _zero_price),
            ),
        ),
        IntegrationCase(
            "07",
            "Versandkosten",
            "REMARK delivery_method und shipping_fee",
            "Zweimal importieren: ohne und mit angelegter Nebenleistung „UPS“",
            "Ohne Nebenleistung: Versandart und 8,90 in der Nachbemerkung; mit Nebenleistung "
            "„UPS“: "
            "eigene Auftragsposition 8,90",
            ("OQ-11",),
            _order(
                "07",
                shipping_method=Field.found("UPS", _SURE),
                shipping_fee=Field.found(Decimal("8.90"), _SURE),
            ),
        ),
        *[
            IntegrationCase(
                f"08{suffix}",
                f"Zahlungsart {label}",
                "PAYMENT-Abbildung",
                "Importieren und übernehmen",
                f"Zahlungsart im Auftrag: {expected}",
                ("OQ-12",) if method is PaymentMethod.DIRECT_DEBIT else (),
                _order(f"08{suffix}", payment_method=Field.found(method, _SURE)),
            )
            for suffix, label, method, expected in (
                ("a", "Rechnung", PaymentMethod.INVOICE, "Rechnung"),
                ("b", "Vorkasse", PaymentMethod.PREPAYMENT, "Vorkasse"),
                ("c", "Nachnahme", PaymentMethod.CASH_ON_DELIVERY, "Nachnahme"),
                ("d", "Barzahlung", PaymentMethod.CASH, "Barzahlung"),
                ("e", "Bankeinzug", PaymentMethod.DIRECT_DEBIT, "Bankverbindung ohne Bankdaten"),
            )
        ],
        IntegrationCase(
            "09",
            "Unbekannte Artikelnummer",
            "Produkt blockiert; Diagnosedatei zeigt Lexware-Verhalten",
            "Nur Diagnosedatei 09-unbekannt.xml importieren",
            "Produkt: kein Export (EXP_ARTICLE_UNRESOLVED). Lexware laut LX-SPEC: Meldung „ist "
            "nicht "
            "als Stammartikel vorhanden“; Rückfrage oder Abbruch dokumentieren",
            ("OQ-19",),
            _order("09", lines=(_line(1, Article(UNKNOWN_ARTICLE, "Unbekannt"), "1", "10.00"),)),
            diagnostics=(
                ("09-unbekannt.xml", "SUPPLIER_AID ohne Stammartikel (OQ-19)", _unknown_article),
            ),
        ),
        IntegrationCase(
            "10",
            "Große Bestellung",
            f"{LARGE_ORDER_LINES} Positionen, Laufzeit, Vollständigkeit",
            "Importieren, Zeit messen, Positionsanzahl und Summe prüfen",
            f"{LARGE_ORDER_LINES} Positionen in beliebiger Reihenfolge (LX-SPEC), Summe laut "
            "Erwartungsdatei; bei Lagerartikeln ggf. Rückfrage zu negativem Bestand",
            ("OQ-20",),
            _order(
                "10",
                lines=tuple(
                    _line(i, TEST_ARTICLES[i % 3], str(1 + i % 5), str(TEST_ARTICLES[i % 3].price))
                    for i in range(1, LARGE_ORDER_LINES + 1)
                ),
            ),
        ),
    ]


def _summary(address: Address | None) -> str:
    return address.summary() if address else ""


def _label(method: PaymentMethod | None) -> str:
    return method.label if method else ""


def write_package(directory: Path, profile: ImportProfile) -> list[dict[str, object]]:
    """Schreibt alle Testfälle; gibt eine Übersicht für den Testplan zurück."""
    adapter = LexwareExportAdapter()
    validator = ExportValidator(adapter.spec)
    overview = []
    for case in build_cases():
        folder = directory / f"{case.number}"
        folder.mkdir(parents=True, exist_ok=True)
        case_profile = replace(profile, export_encoding=case.encoding)
        number = case.order.document_number or ""
        prepared = adapter.prepare(case.order, case_profile, now=GENERATED_AT, number=number)
        xml = (
            adapter.render(prepared.document, case.encoding, notation=case.notation)
            if prepared.document
            else None
        )
        result = validator.validate(prepared, xml=xml, file_name=None, encoding=case.encoding)
        errors = [f"{f.code}: {f.message}" for f in result.errors()]
        notes = [
            f"{f.code}: {f.message}" for f in result.findings if f.severity is not Severity.ERROR
        ]
        files = []
        if xml is not None and not errors:
            name = f"{case.order.document_number}.xml"
            (folder / name).write_bytes(xml)
            files.append(name)
        for file_name, _description, transform in case.diagnostics:
            simple = replace(case.order, lines=(_line(1, TEST_ARTICLES[0], "1", "10.00"),))
            base = adapter.prepare(simple, case_profile, now=GENERATED_AT, number=number)
            if base.document is not None:
                text = adapter.render(base.document, case.encoding).decode(case.encoding)
                (folder / file_name).write_bytes(transform(text).encode(case.encoding))
                files.append(file_name)
        entry: dict[str, object] = {
            "test": case.number,
            "title": case.title,
            "encoding": case.encoding,
            "notation": case.notation or "cdata",
            "files": files,
            "export_blocked_by_product": errors,
            "product_notes": notes,
            "open_questions": list(case.open_questions),
            "input": {
                "document_number": case.order.document_number,
                "customer_reference": case.order.customer_reference.value,
                "invoice_address": _summary(case.order.invoice_address.value),
                "delivery_same_as_invoice": case.order.delivery_same_as_invoice,
                "payment": _label(case.order.payment_method.value),
                "lines": len(case.order.lines),
            },
        }
        (folder / "input.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        overview.append(
            entry
            | {
                "purpose": case.purpose,
                "procedure": case.procedure,
                "expectation": case.lexware_expectation,
            }
        )
    return overview
