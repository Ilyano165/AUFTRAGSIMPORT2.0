from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from icware_auftragsimport.domain.errors import CatalogError
from icware_auftragsimport.domain.findings import Severity
from icware_auftragsimport.domain.models import (
    Article,
    ArticleMatch,
    MatchStatus,
    MatchStrategy,
    OrderLine,
)
from icware_auftragsimport.domain.provenance import Confidence, Evidence, Field
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import Database
from icware_auftragsimport.services.catalog import (
    CatalogService,
    read_catalog_csv,
    read_catalog_json,
)
from icware_auftragsimport.services.matching import (
    ArticleMatcher,
    Catalog,
    normalize_name,
    normalize_number,
)
from support import ARTICLES, CATALOG


def _line(
    description: str, hint: str | None = None, confidence: Confidence = Confidence.CERTAIN
) -> OrderLine:
    article_hint: Field[str] = (
        Field.found(hint, Evidence("test", "Test", confidence)) if hint else Field.unknown()
    )
    return OrderLine(
        1,
        description,
        Field.found(Decimal(1), Evidence("t", "t", Confidence.CERTAIN)),
        article_hint=article_hint,
    )


def _match(
    description: str, hint: str | None = None, confidence: Confidence = Confidence.CERTAIN
) -> ArticleMatch:
    return ArticleMatcher(CATALOG).match(_line(description, hint, confidence))


def test_normalization() -> None:
    assert normalize_number(" yt-11 ybor/01 ") == "YT11YBOR01"
    assert normalize_name("YelloBlade  Grün!") == "yelloblade gruen"
    assert normalize_name("Café Crème") == "cafe creme"


def test_explicit_number_wins_and_ignores_formatting() -> None:
    result = _match("irgendwas", "yt-11-ybor-01")
    assert (result.status, result.strategy) == (MatchStatus.MATCHED, MatchStrategy.EXPLICIT_NUMBER)
    assert result.article is not None and result.article.number == "YT11YBOR01"


def test_explicit_unknown_number_is_never_matched() -> None:
    result = _match("YelloBlade Orange", "YT99XX")
    assert result.status is MatchStatus.NEEDS_REVIEW
    assert result.article is None
    assert [c.article.number for c in result.candidates] == ["YT11YBOR01"]
    assert "YT99XX steht nicht im Katalog" in result.reason


def test_explicit_unknown_number_without_any_suggestion_is_unknown() -> None:
    assert _match("Gibt es nicht", "ZZ-999").status is MatchStatus.UNKNOWN


def test_likely_code_not_in_catalog_falls_back_to_name() -> None:
    result = _match("Rakel Gold", "YT77ABC", Confidence.LIKELY)
    assert (result.status, result.strategy) == (MatchStatus.MATCHED, MatchStrategy.NAME)


@pytest.mark.parametrize(
    ("text", "strategy", "number"),
    [
        ("Blade Orange", MatchStrategy.ALIAS, "YT11YBOR01"),
        ("rakel gold", MatchStrategy.NAME, "YT20RAK01"),
        ("YelloBlade Gruen", MatchStrategy.NORMALIZED_NAME, "YT11YBGR01"),
        ("Filz", MatchStrategy.ALIAS, "YT15FILZ02"),
    ],
)
def test_strategy_order(text: str, strategy: MatchStrategy, number: str) -> None:
    result = _match(text)
    assert (result.status, result.strategy) == (MatchStatus.MATCHED, strategy)
    assert result.article is not None and result.article.number == number


def test_fuzzy_never_selects_silently() -> None:
    result = _match("YelloBlade Orang")
    assert result.status is MatchStatus.NEEDS_REVIEW
    assert result.strategy is MatchStrategy.FUZZY
    assert result.article is None
    assert result.candidates[0].article.number == "YT11YBOR01"
    assert result.candidates[0].score >= 0.8


def test_ambiguous_alias_needs_review() -> None:
    catalog = Catalog(
        [Article("A1", "Wasser still", ("Wasser",)), Article("A2", "Wasser medium", ("Wasser",))]
    )
    result = ArticleMatcher(catalog).match(_line("Wasser"))
    assert result.status is MatchStatus.NEEDS_REVIEW
    assert [c.article.number for c in result.candidates] == ["A1", "A2"]


def test_inactive_articles_are_ignored() -> None:
    assert _match("Altartikel", "OLD-1").status is MatchStatus.UNKNOWN
    assert len(CATALOG.articles) == len(ARTICLES) - 1


def test_manual_match_is_kept() -> None:
    manual = ArticleMatch(MatchStatus.MANUAL, ARTICLES[0], MatchStrategy.MANUAL, "vom Benutzer")
    line = OrderLine(1, "Rakel Gold", match=manual)
    assert ArticleMatcher(CATALOG).match_line(line).match == manual


def test_known_name_for_bare_lines() -> None:
    assert CATALOG.knows_name("Apfelschorle")
    assert not CATALOG.knows_name("Apfel")


def test_csv_import_with_windows_encoding_and_semicolons() -> None:
    data = (
        "Artikelnummer;Bezeichnung;Alias;Einheit;MwSt;Preis\n"
        "GT-1;Wasser Stille Quelle;Wasser still|Stilles;Kiste;19;5,49\n"
        "GT-2;Saft Ä;;Kiste;7 %;12,00\n"
    ).encode("cp1252")
    result = read_catalog_csv(data)
    assert not result.errors
    assert result.articles[0] == Article(
        "GT-1",
        "Wasser Stille Quelle",
        ("Wasser still", "Stilles"),
        "Kiste",
        Decimal(19),
        Decimal("5.49"),
    )
    assert result.articles[1].name == "Saft Ä" and result.articles[1].tax_rate == Decimal(7)


def test_csv_with_custom_columns() -> None:
    data = b"SKU,Name\nX1,Eins\n"
    assert read_catalog_csv(data).articles[0].name == "Eins"
    custom = read_catalog_csv(b"Nr;Kurztext\nX1;Eins\n", {"number": "Nr", "name": "Kurztext"})
    assert custom.articles[0].number == "X1"


def test_csv_without_required_columns_fails_with_explanation() -> None:
    with pytest.raises(CatalogError) as info:
        read_catalog_csv(b"Foo;Bar\n1;2\n")
    assert "Spalte für Artikelnummer nicht gefunden" in str(info.value)
    assert "bisher aktive Katalog bleibt unverändert" in str(info.value)


def test_catalog_validation_findings() -> None:
    data = (
        b"Artikelnummer;Bezeichnung;Alias;MwSt;Preis\n"
        b"A-1;Rakel;;19;1,00\n"
        b"A1;Rakel 2;Rakel;19;1,00\n"
        b";Ohne Nummer;;19;1,00\n"
        b"B1;;;150;-2,00\n"
    )
    result = read_catalog_csv(data)
    errors = {issue.render() for issue in result.errors}
    assert "Artikelnummer A1 mehrfach (Zeilen 2, 3)" in errors
    assert "Zeile 4: Artikelnummer fehlt" in errors
    assert {
        "Zeile 5: Bezeichnung fehlt",
        "Zeile 5: Steuersatz außerhalb 0–100 %",
        "Zeile 5: Preis ist negativ",
    } <= errors
    assert any(
        i.severity is Severity.WARNING and "mehreren Artikeln" in i.message for i in result.issues
    )


def test_json_catalog() -> None:
    data = json.dumps(
        [{"number": "J1", "name": "Json", "aliases": ["J"], "tax_rate": "19"}]
    ).encode()
    result = read_catalog_json(data)
    assert result.articles[0].aliases == ("J",) and not result.errors


def test_catalog_service_versions_and_refuses_broken_catalogs(
    tmp_path: Path, clock: FixedClock
) -> None:
    database = Database(tmp_path / "c.db")
    conn: sqlite3.Connection = database.connect()
    database.migrate(conn)
    service = CatalogService(conn, clock)
    assert service.active_catalog("standard") is None
    first = service.store(
        "standard", "v1.csv", read_catalog_csv(b"Artikelnummer;Bezeichnung\nA1;Eins\n")
    )
    second = service.store(
        "standard", "v2.csv", read_catalog_csv(b"Artikelnummer;Bezeichnung\nA2;Zwei\n")
    )
    with pytest.raises(CatalogError):
        service.store(
            "standard", "kaputt.csv", read_catalog_csv(b"Artikelnummer;Bezeichnung\n;Leer\n")
        )
    active = service.active_catalog("standard")
    assert active is not None and [a.number for a in active.articles] == ["A2"]
    service.activate("standard", first)
    rolled_back = service.active_catalog("standard")
    assert rolled_back is not None and [a.number for a in rolled_back.articles] == ["A1"]
    assert second == first + 1
