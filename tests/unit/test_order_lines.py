from __future__ import annotations

from decimal import Decimal

import pytest

from icware_auftragsimport.domain.provenance import Confidence, FieldState
from icware_auftragsimport.parsers.common import Consumed
from icware_auftragsimport.parsers.order_lines import OrderLineParser, OrderLineResult
from support import context


def _parse(text: str) -> OrderLineResult:
    return OrderLineParser().parse(context(text), Consumed())


def _summary(result: OrderLineResult) -> list[tuple[str, Decimal | None, str, str | None]]:
    return [
        (ln.description, ln.quantity.value, ln.unit, ln.article_hint.value) for ln in result.lines
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("5 x YelloBlade Orange", ("YelloBlade Orange", Decimal(5), "", None)),
        ("5x YelloBlade Orange", ("YelloBlade Orange", Decimal(5), "", None)),
        ("10 Stk. Rakel Gold", ("Rakel Gold", Decimal(10), "Stk", None)),
        ("2 Kartons Wischer (Art.-Nr. W-100)", ("Wischer", Decimal(2), "Karton", "W-100")),
        ("10 Kisten Wasser Classic", ("Wasser Classic", Decimal(10), "Kiste", None)),
        ("YelloBlade Grün – 7 Stück", ("YelloBlade Grün", Decimal(7), "Stk", None)),
        ("Rakel Silber x 4", ("Rakel Silber", Decimal(4), "", None)),
        ("YT11 – YelloBlade – Menge: 5", ("YelloBlade", Decimal(5), "", "YT11")),
        ("- 3 x Filz", ("Filz", Decimal(3), "", None)),
        ("1. 3 x Filz", ("Filz", Decimal(3), "", None)),
        ("2,5 kg Granulat", ("Granulat", Decimal("2.5"), "kg", None)),
    ],
)
def test_single_line_formats(line: str, expected: tuple[str, Decimal, str, str | None]) -> None:
    assert _summary(_parse(line)) == [expected]


def test_code_token_is_only_a_likely_hint_and_price_is_read() -> None:
    [line] = _parse("10 Stk. YT20RAK01 Rakel Gold à 12,50 €").lines
    assert line.article_hint.value == "YT20RAK01"
    assert line.article_hint.evidence is not None
    assert line.article_hint.evidence.confidence is Confidence.LIKELY
    assert line.unit_price.value == Decimal("12.50")
    assert line.description == "Rakel Gold"


def test_attribute_lines_belong_only_to_the_line_directly_above() -> None:
    result = _parse("5 x Rakel\n3 x Filz\nArt.-Nr.: F1\nFarbe: Schwarz\n2 x Blade")
    assert [(ln.description, ln.article_hint.value, ln.detail) for ln in result.lines] == [
        ("Rakel", None, ""),
        ("Filz", "F1", "Farbe: Schwarz"),
        ("Blade", None, ""),
    ]
    assert result.lines[1].source is not None
    assert result.lines[1].source.label == "Mailzeilen 2–4"


def test_duplicate_lines_stay_separate() -> None:
    result = _parse("5 x Rakel\n2 x Filz\n5 x Rakel")
    assert [ln.position for ln in result.lines] == [1, 2, 3]
    assert result.lines[0].description == result.lines[2].description


def test_labeled_blocks() -> None:
    result = _parse(
        "Artikel: YelloBlade Orange\nArt.-Nr.: YT11YBOR01\nMenge: 5 Stück\n\n"
        "Bezeichnung: Filz\nMenge: 2"
    )
    assert _summary(result) == [
        ("YelloBlade Orange", Decimal(5), "Stk", "YT11YBOR01"),
        ("Filz", Decimal(2), "", None),
    ]


def test_orphan_article_number_is_reported() -> None:
    result = _parse("Art.-Nr.: YT11YBOR01\n\nDanke")
    assert result.lines == ()
    assert [n.code for n in result.notes] == ["ORPHAN_ARTICLE_NUMBER"]


@pytest.mark.parametrize(
    "table",
    [
        "Art.-Nr.\tBezeichnung\tMenge\nYT11\tBlade\t5\nYT15\tFilz\t2",
        "Art.-Nr.;Bezeichnung;Menge\nYT11;Blade;5\nYT15;Filz;2",
        "| Art.-Nr. | Bezeichnung | Menge |\n|---|---|---|\n"
        "| YT11 | Blade | 5 |\n| YT15 | Filz | 2 |",
        "Art.-Nr.     Bezeichnung     Menge\n"
        "YT11         Blade           5\n"
        "YT15         Filz            2",
    ],
)
def test_tables(table: str) -> None:
    assert _summary(_parse(table)) == [
        ("Blade", Decimal(5), "", "YT11"),
        ("Filz", Decimal(2), "", "YT15"),
    ]


def test_ambiguous_quantity_needs_review() -> None:
    [line] = _parse("Art.-Nr.;Bezeichnung;Menge\nYT15;Filz;1.000").lines
    assert line.quantity.value == Decimal(1000)
    assert line.quantity.state is FieldState.NEEDS_REVIEW


@pytest.mark.parametrize(
    "noise",
    [
        "3 Tage Lieferzeit wären super",
        "Lieferung in KW 32",
        "Wir haben 3 Standorte.",
        "Versand am 14.06. bitte",
        "Bestellnummer 251843",
        "Musterweg 12",
        "Telefon 0231 12345",
        "Versandkosten: 8,90 €",
        "Lieferzeit: 3 Tage",
        "Wir brauchen die Ware bis 12 Uhr",
    ],
)
def test_noise_never_becomes_a_line(noise: str) -> None:
    result = _parse(noise)
    assert result.lines == ()
    assert result.weak == ()


def test_bare_quantity_is_only_weak() -> None:
    result = _parse("5 x Rakel\n4 Filz Schwarz\n\n7 Blade Orange")
    assert [ln.description for ln in result.lines] == ["Rakel"]
    assert [(w.line.description, w.adjacent_to_item) for w in result.weak] == [
        ("Filz Schwarz", True),
        ("Blade Orange", False),
    ]
