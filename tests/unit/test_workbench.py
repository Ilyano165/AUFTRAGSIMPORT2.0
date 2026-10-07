"""Anwendungsschicht der Oberfläche auf dem Demobestand."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from icware_auftragsimport.app.demo import build_demo
from icware_auftragsimport.app.presentation import Category
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.domain.provenance import Field
from icware_auftragsimport.domain.status import ExportMode, OrderStatus
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.services.export_service import ExportResult

NOW = datetime(2026, 10, 1, 10, 45, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture
def workbench(tmp_path: Path) -> Workbench:
    database, settings = build_demo(tmp_path / "demo", NOW)
    return Workbench(database.connect(), settings, FixedClock(NOW))


def test_demo_covers_every_area(workbench: Workbench) -> None:
    counts = workbench.counts(workbench.rows())
    assert counts == {
        Category.INBOX: 8,
        Category.READY: 4,
        Category.REVIEW: 3,
        Category.DONE: 3,
        Category.ERROR: 1,
    }
    plan = workbench.export_plan()
    assert (plan.summary.ready, plan.summary.blocked) == (3, 0)


def test_fix_save_approve_flow(workbench: Workbench) -> None:
    order = workbench.snapshot("order-003").order
    address = replace(order.delivery_address.value, house_number="1")
    saved = workbench.save(replace(order, delivery_address=Field.manual(address)))
    assert saved.order.status is OrderStatus.READY
    assert saved.order.revision == order.revision + 1
    approved = workbench.approve("order-003")
    assert approved.order.status is OrderStatus.APPROVED
    assert approved.order.document_number == "AU-2026-000007"
    assert workbench.export_plan().summary.ready == 4


def test_cannot_approve_incomplete_or_edit_approved(workbench: Workbench) -> None:
    with pytest.raises(ValueError, match="vollständig geprüfte"):
        workbench.approve("order-002")
    with pytest.raises(ValueError, match="schreibgeschützt"):
        workbench.save(workbench.snapshot("order-004").order)


def test_ignore_moves_to_done(workbench: Workbench) -> None:
    assert workbench.ignore("order-002").order.status is OrderStatus.IGNORED
    assert workbench.counts(workbench.rows())[Category.DONE] == 4


def test_mail_text_and_catalog_lookup(workbench: Workbench) -> None:
    snapshot = workbench.snapshot("order-002")
    assert "Unsere Bestellnummer: BS-44871" in workbench.mail_text(snapshot)
    article = workbench.article("mw-0710")
    assert article is not None
    assert article.number == "MW-0710"
    assert workbench.article("XX-0000") is None


def test_test_export_of_all_ready_orders(workbench: Workbench, tmp_path: Path) -> None:
    assert not workbench.production_allowed
    plan = workbench.export_plan()
    reports = workbench.export(plan.ready_ids, ExportMode.TEST)
    assert [r.result for r in reports] == [ExportResult.SUCCESS] * 3
    assert len(list((tmp_path / "demo" / "lexware-test").glob("*.xml"))) == 3
    assert workbench.export_history("order-004")
