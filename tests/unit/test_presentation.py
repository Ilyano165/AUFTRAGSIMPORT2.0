"""Darstellungslogik: Status, Bereiche, Formate, Befundtexte, Exportzusammenfassung."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from icware_auftragsimport.app.demo import build_demo
from icware_auftragsimport.app.presentation import (
    CATEGORIES,
    STATUS_LOOK,
    Category,
    DetailTab,
    ExportSummary,
    format_money,
    format_quantity,
    format_received,
    present_findings,
)
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.domain.findings import Severity
from icware_auftragsimport.domain.status import OrderStatus
from icware_auftragsimport.infrastructure.clock import FixedClock

NOW = datetime(2026, 10, 1, 10, 45, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture
def workbench(tmp_path: Path) -> Workbench:
    database, settings = build_demo(tmp_path / "demo", NOW)
    return Workbench(database.connect(), settings, FixedClock(NOW))


def test_every_status_has_distinct_label_and_category() -> None:
    assert set(STATUS_LOOK) == set(OrderStatus)
    labels = [look.label for look in STATUS_LOOK.values()]
    assert len(labels) == len(set(labels))
    for status in OrderStatus:
        assert any(status in category.statuses for category in CATEGORIES), status


def test_categories_match_requested_order_and_inbox_holds_all_open() -> None:
    assert [c.label for c in CATEGORIES] == [
        "Posteingang",
        "Bereit",
        "Prüfung erforderlich",
        "Erledigt",
        "Fehler",
    ]
    inbox = CATEGORIES[0].statuses
    for category in CATEGORIES[1:]:
        if category.key is not Category.DONE:
            assert category.statuses <= inbox
    assert not inbox & CATEGORIES[3].statuses


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 1, 9, 5, tzinfo=NOW.tzinfo), "09:05"),
        (datetime(2026, 9, 30, 16, 0, tzinfo=NOW.tzinfo), "Gestern 16:00"),
        (datetime(2026, 9, 28, 8, 0, tzinfo=NOW.tzinfo), "Mo 28.09."),
        (datetime(2026, 9, 1, 8, 0, tzinfo=NOW.tzinfo), "01.09.2026"),
        (None, "–"),
    ],
)
def test_received_like_windows_mail_clients(moment: datetime | None, expected: str) -> None:
    assert format_received(moment, NOW) == expected


def test_german_number_formats() -> None:
    assert format_money(Decimal("1234.5")) == "1.234,50 €"
    assert format_quantity(Decimal("2.50")) == "2,5"
    assert format_quantity(Decimal("12.000")) == "12"


def test_export_summary_texts_and_singular() -> None:
    assert ExportSummary(3, 0).ready_text == "3 Aufträge bereit"
    assert ExportSummary(3, 0).blocked_text == "0 Aufträge mit Fehlern"
    assert ExportSummary(1, 1).ready_text == "1 Auftrag bereit"
    assert ExportSummary(1, 1).blocked_text == "1 Auftrag mit Fehlern"
    assert not ExportSummary(0, 2).can_start


def test_missing_article_reads_what_where_and_what_to_do(workbench: Workbench) -> None:
    snapshot = workbench.snapshot("order-002")
    issue = present_findings(snapshot.validation, snapshot.order)[0]
    assert issue.title == "Artikelnummer fehlt"
    assert issue.steps[0] == "Position 4"
    assert issue.steps[-1] == "Benutzeraktion erforderlich"
    assert issue.tab is DetailTab.LINES
    assert issue.line_index == 3


def test_incomplete_delivery_address_names_missing_part(workbench: Workbench) -> None:
    snapshot = workbench.snapshot("order-003")
    issue = next(
        i
        for i in present_findings(snapshot.validation, snapshot.order)
        if i.severity is Severity.ERROR
    )
    assert issue.title == "Lieferadresse unvollständig"
    assert issue.steps == ("Hausnummer fehlt", "Benutzeraktion erforderlich")
    assert issue.tab is DetailTab.DELIVERY


def test_done_orders_show_no_problems(workbench: Workbench) -> None:
    ignored = next(r for r in workbench.rows() if r.status is OrderStatus.IGNORED)
    assert (ignored.errors, ignored.warnings, ignored.problems_text) == (0, 0, "")


def test_row_search_matches_all_terms(workbench: Workbench) -> None:
    row = workbench.snapshot("order-006").row
    assert row.matches("schulte 4500018822")
    assert not row.matches("schulte bonn")
