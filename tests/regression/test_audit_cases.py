"""Regressionstests zu den Audit-Befunden aus Version 1.1.0 (Audit vom 30.09.2026).

Jeder Test stellt das damalige Fehlverhalten nach und belegt das neue Verhalten.
Befunde zu Mailabruf, Archivierung und Export folgen mit den Iterationen 4 und 5.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from icware_auftragsimport.domain.models import Address, MailMetadata, MatchStatus, PaymentMethod
from icware_auftragsimport.domain.provenance import FieldState
from icware_auftragsimport.domain.status import MailState
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import Database, transaction
from icware_auftragsimport.infrastructure.repositories import CounterRepository, MailRepository
from icware_auftragsimport.parsers.text import decode_payload, normalize
from icware_auftragsimport.services.idempotency import MailDuplicate, MailIdentity, check_mail
from icware_auftragsimport.services.numbering import next_document_number
from support import analyze


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    database = Database(tmp_path / "r.db")
    connection = database.connect()
    database.migrate(connection)
    return connection


def test_c03_unknown_charset_does_not_abort() -> None:
    """C-03: ``LookupError`` bei unbekanntem Zeichensatz brach den ganzen Abruf ab."""
    decoded = decode_payload("5 x Rakel für Köln".encode("cp1252"), "x-unknown-charset")
    assert decoded.text == "5 x Rakel für Köln"
    assert decoded.fallback_used


def test_c09_document_numbers_never_collide(conn: sqlite3.Connection) -> None:
    """C-09: ORDER_ID aus Zeitstempel kollidierte bei zwei Exporten in derselben Sekunde."""
    counters = CounterRepository(conn)
    with transaction(conn):
        numbers = {next_document_number(counters, "AI", 2026) for _ in range(50)}
    assert len(numbers) == 50


def test_c10_same_mail_is_recognized_by_several_keys(
    conn: sqlite3.Connection, clock: FixedClock
) -> None:
    """C-10: Dedupe nur über UID + order_id; eine verschobene Mail galt als neu."""
    meta = MailMetadata(
        id="m1", account_id="a", folder="INBOX", uidvalidity=1, uid=5, message_id="<x@kunde>",
        content_hash="h", raw_sha256="r", sender="k@kunde.de", sender_name="", subject="B",
        date_header=None, size=1,
    )  # fmt: skip
    MailRepository(conn).insert(meta, MailState.EXTRACTED, clock.now())
    moved = MailIdentity("a", "Archiv", 9, 77, "<x@kunde>", "h")
    assert check_mail(MailRepository(conn), moved).kind is MailDuplicate.SAME_MESSAGE_ID


def test_c14_company_only_is_not_ready() -> None:
    """C-14: ``is_ready`` akzeptierte eine Anschrift, die nur aus dem Firmennamen bestand."""
    analysis = analyze("Rechnungsadresse: Nur Firma GmbH\n\n5 x Rakel Gold\nZahlungsart: Rechnung")
    assert "ADDRESS_INCOMPLETE" in analysis.error_codes()


def test_c15_article_number_does_not_shift_to_previous_line() -> None:
    """C-15: Look-ahead hängte die Art.-Nr. der nächsten Position an die vorige."""
    analysis = analyze("5 x Rakel Gold\n3 x Filz\nArt.-Nr.: YT15FILZ02")
    rakel, filz = analysis.order.lines
    assert rakel.article_hint.value is None
    assert rakel.match.article is not None and rakel.match.article.number == "YT20RAK01"
    assert filz.article_hint.value == "YT15FILZ02"


def test_c16_unknown_article_number_is_not_counted_as_matched() -> None:
    """C-16: Unbekannte Artikelnummern galten als zugeordnet."""
    analysis = analyze("5 x Rakel Platin\nArt.-Nr.: YT99XX")
    [line] = analysis.order.lines
    assert line.match.status is MatchStatus.UNKNOWN
    assert "LINE_ARTICLE_UNKNOWN" in analysis.error_codes()


def test_c17_payment_is_not_taken_from_unrelated_free_text() -> None:
    """C-17: Zahlungsart wurde aus beliebigen Wörtern im Fließtext abgeleitet."""
    analysis = analyze(
        "5 x Rakel Gold\nDie Überweisung zur letzten Rechnung ist raus, der Artikel ist verfügbar."
    )
    assert analysis.order.payment_method.state is FieldState.UNKNOWN
    assert "PAYMENT_METHOD_MISSING" in analysis.error_codes()


def test_c18_forwarded_mail_is_read_without_quote_garbage() -> None:
    """C-18: Weitergeleitete Mails mit „> “ ergaben Datenmüll und falschen Absender."""
    analysis = analyze(
        "Bitte erfassen\n\nAnfang der weitergeleiteten Nachricht:\n\n"
        "> Von: Kunde <einkauf@baustoff-meyer.de>\n> Betreff: Bestellung\n>\n"
        "> 5 x Rakel Gold\n> Zahlungsart: Vorkasse",
        "WG: Bestellung",
        "kollege@lieferant.de",
    )
    [line] = analysis.order.lines
    assert line.description == "Rakel Gold"
    assert analysis.order.payment_method.value is PaymentMethod.PREPAYMENT
    assert analysis.order.customer_key == "dom:baustoff-meyer.de"


def test_c19_duplicate_lines_are_not_merged() -> None:
    """C-19: Gleiche Positionen wurden still zusammengeführt."""
    analysis = analyze("5 x Rakel Gold\n2 x Filz\n5 x Rakel Gold")
    assert len(analysis.order.lines) == 3
    assert "LINE_DUPLICATE_ARTICLE" in analysis.codes()


def test_c20_sentences_are_not_positions() -> None:
    """C-20: Sätze mit Zahl am Anfang wurden zu Positionen."""
    analysis = analyze("5 x Rakel Gold\n\n3 Tage Lieferzeit wären super\n\n2 Mitarbeiter holen ab")
    assert [line.description for line in analysis.order.lines] == ["Rakel Gold"]


def test_c20_uncertain_line_is_shown_not_dropped() -> None:
    """Kandidaten ohne Einheit verschwinden nicht still, sondern werden als Hinweis gezeigt."""
    analysis = analyze("5 x Rakel Gold\n\n4 Sonderanfertigungen nach Zeichnung")
    assert [n.code for n in analysis.result.notes] == ["POSSIBLE_LINE_SKIPPED"]


def test_c21_sentence_with_rechnung_an_is_not_an_address() -> None:
    """C-21: „Bitte schicken Sie die Rechnung an:“ eröffnete eine Rechnungsanschrift."""
    analysis = analyze(
        "Bitte schicken Sie die Rechnung an:\nbuchhaltung@kunde.de\n\n5 x Rakel Gold"
    )
    assert analysis.order.invoice_address.state is FieldState.UNKNOWN


def test_c22_vat_line_is_not_an_article_number() -> None:
    """C-22: Eine USt-IdNr. unter der Position wurde zur Artikelnummer."""
    analysis = analyze("5 x Rakel Gold\nUSt-IdNr.: DE123456789")
    [line] = analysis.order.lines
    assert line.article_hint.value is None
    assert analysis.order.vat_id.value == "DE123456789"


def test_c23_versand_am_is_not_a_shipping_method() -> None:
    """C-23: „Versand am 14.06.“ wurde als Versandart übernommen."""
    analysis = analyze("5 x Rakel Gold\nVersand am 14.06. bitte")
    assert analysis.order.shipping_method.state is FieldState.UNKNOWN


def test_c24_thousand_separator_is_not_read_as_decimal() -> None:
    """C-24: „1.234“ wurde als 1,234 gelesen."""
    analysis = analyze("Art.-Nr.;Bezeichnung;Menge\nYT15FILZ02;Filz;1.234")
    [line] = analysis.order.lines
    assert line.quantity.value == Decimal(1234)
    assert "LINES[0].QUANTITY_REVIEW" in analysis.error_codes()


def test_c25_four_digit_postcode_is_not_silently_german() -> None:
    """C-25: Vierstellige PLZ wurden als ungültig verworfen oder Deutschland zugeordnet."""
    analysis = analyze("Rechnungsadresse:\nAlpen Bau GmbH\nWeg 3\n6020 Innsbruck\n\n5 x Rakel Gold")
    address = analysis.order.invoice_address
    assert address.state is FieldState.NEEDS_REVIEW
    assert address.value == Address(
        company="Alpen Bau GmbH",
        street="Weg",
        house_number="3",
        postal_code="6020",
        city="Innsbruck",
    )


def test_c29_control_characters_are_removed_before_parsing() -> None:
    """C-29: Steuerzeichen landeten ungefiltert im XML."""
    normalized = normalize("5 x Rakel\x0b Gold\x1f")
    assert normalized.lines[0].text == "5 x Rakel Gold"
