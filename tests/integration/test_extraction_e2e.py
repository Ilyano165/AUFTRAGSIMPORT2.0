"""Ende-zu-Ende: realistische Mails durch Normalisierung, Erkennung, Zuordnung, Validierung."""

from __future__ import annotations

from decimal import Decimal

from icware_auftragsimport.domain.models import MatchStatus, PaymentMethod
from icware_auftragsimport.domain.provenance import FieldState
from support import PROFILE_WITH_DEFAULT, analyze

BEVERAGE_ORDER = """Guten Morgen,

wir bestellen für kommende Woche:

10 Kisten Wasser Classic
5 Kisten Wasser Medium
8 Kisten Apfelschorle

Bestellnummer: GH-2026-0815
Zahlungsart: Rechnung
Lieferadresse wie Rechnungsadresse

Rechnungsadresse:
Getränkemarkt Schulz e.K.
Hauptstraße 12
53783 Eitorf

Viele Grüße
Karl Schulz
Tel. 02243 12345"""

OUTLOOK_HTML = """<html><body><p>Sehr geehrte Damen und Herren,</p><p>anbei unsere Bestellung.</p>
<table><tr><th>Art.-Nr.</th><th>Bezeichnung</th><th>Menge</th></tr>
<tr><td>YT20RAK01</td><td>Rakel Gold</td><td>12</td></tr>
<tr><td>YT11YBOR01</td><td>YelloBlade Orange</td><td>3</td></tr></table>
<p>Bestell-Nr.: PO-77812<br>Versandart: DHL<br>Zahlungsart: per Nachnahme</p>
<p>Rechnungs- und Lieferadresse:<br>Druckerei Beispiel GmbH &amp; Co. KG<br>Abteilung Einkauf<br>
Am Markt 3 b<br>D-10115 Berlin</p>
<p>Mit freundlichen Grüßen<br>Anna von Berg</p>
<blockquote><div>Am 20.09.2026 schrieb Vertrieb:</div><div>5 x Altartikel</div></blockquote>
</body></html>"""


def test_beverage_wholesale_order_is_ready_for_approval() -> None:
    analysis = analyze(BEVERAGE_ORDER, "Bestellung KW 41")
    order = analysis.order
    assert analysis.error_codes() == set()
    assert [
        (ln.quantity.value, ln.unit, ln.match.article and ln.match.article.number)
        for ln in order.lines
    ] == [
        (Decimal(10), "Kiste", "GT-1001"),
        (Decimal(5), "Kiste", "GT-1002"),
        (Decimal(8), "Kiste", "GT-2001"),
    ]
    assert order.customer_reference.value == "GH-2026-0815"
    assert order.delivery_same_as_invoice
    assert order.contact.value is not None and order.contact.value.last_name == "Schulz"


def test_outlook_html_order_with_reply_history() -> None:
    analysis = analyze(OUTLOOK_HTML, "Bestellung", "a.berg@druckerei.de", html=True)
    order = analysis.order
    assert analysis.error_codes() == set()
    assert [ln.match.strategy.value for ln in order.lines] == ["explicit_number", "explicit_number"]
    assert order.payment_method.value is PaymentMethod.CASH_ON_DELIVERY
    assert order.shipping_method.value == "DHL"
    address = order.invoice_address.value
    assert address is not None
    assert (address.company, address.department, address.house_number) == (
        "Druckerei Beispiel GmbH & Co. KG",
        "Abteilung Einkauf",
        "3b",
    )
    assert all("Altartikel" not in ln.description for ln in order.lines)


def test_forwarded_mail_only_with_delivery_address_needs_invoice_confirmation() -> None:
    mail = (
        "Hallo Jan, kannst du das bitte erfassen?\n\nVon meinem iPhone gesendet\n\n"
        "Anfang der weitergeleiteten Nachricht:\n\n"
        "> Von: Thomas Müller <t.mueller@muster-werbung.de>\n> Betreff: Bestellung 4711\n>\n"
        "> 5 x YelloBlade Orange\n> Art.-Nr.: YT11YBOR01\n> 3 x Filzstreifen\n>\n"
        "> Lieferadresse:\n> Muster Werbung GmbH\n> Industriestr. 5\n> 50667 Köln\n>\n"
        "> Zahlung: Vorkasse\n>\n> Mit freundlichen Grüßen\n> Thomas Müller"
    )
    analysis = analyze(mail, "WG: Bestellung 4711", "kollege@yellotools.de")
    assert analysis.error_codes() == {"INVOICE_ADDRESS_REVIEW"}
    assert analysis.order.customer_reference.value == "4711"
    assert analysis.order.contact.value is not None
    assert analysis.order.contact.value.email == "t.mueller@muster-werbung.de"


def test_profile_default_payment_is_visible_not_silent() -> None:
    analysis = analyze(
        BEVERAGE_ORDER.replace("Zahlungsart: Rechnung\n", ""), profile=PROFILE_WITH_DEFAULT
    )
    payment = analysis.order.payment_method
    assert payment.value is PaymentMethod.INVOICE
    assert payment.evidence is not None and payment.evidence.method == "profile_default"
    assert "Standard des Importprofils" in payment.evidence.reason


def test_without_catalog_every_line_needs_an_article() -> None:
    analysis = analyze(BEVERAGE_ORDER, catalog=None)
    assert {ln.match.status for ln in analysis.order.lines} == {MatchStatus.UNKNOWN}
    assert "LINE_ARTICLE_UNKNOWN" in analysis.error_codes()


def test_non_order_mail_is_flagged() -> None:
    analysis = analyze("Hallo, wann ist meine Lieferung da?\nGruß Peter", "Frage")
    assert analysis.result.is_order.state is FieldState.NEEDS_REVIEW
    assert analysis.result.is_order.value is False
    assert "LINES_MISSING" in analysis.error_codes()


def test_every_recognized_value_has_a_source_line() -> None:
    order = analyze(BEVERAGE_ORDER).order
    for field in (order.customer_reference, order.payment_method, order.invoice_address):
        assert field.evidence is not None and field.evidence.source is not None
    assert all(line.source is not None for line in order.lines)
