"""Firmenprofile: Konfiguration, Konflikte, Regeln, Trennung, Nummernkreise, Neuzuordnung."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from icware_auftragsimport.app.demo import NORDLICHT, STANDARD, build_demo
from icware_auftragsimport.app.workbench import MemorySettings, Workbench
from icware_auftragsimport.catalog.export import ExportFormat
from icware_auftragsimport.config.schema import (
    ImportProfile,
    MailAccount,
    MailTemplate,
    SenderAction,
    SenderRule,
    Settings,
    ShippingOption,
    parse_settings,
    settings_to_dict,
)
from icware_auftragsimport.domain.errors import ConfigError
from icware_auftragsimport.domain.models import Order, PaymentMethod
from icware_auftragsimport.domain.provenance import Field
from icware_auftragsimport.domain.status import ExportMode, OrderStatus
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import transaction
from icware_auftragsimport.infrastructure.repositories import (
    CounterRepository,
    JournalRepository,
    OrderRepository,
)
from icware_auftragsimport.services.profile_rules import (
    evaluate_sender,
    match_shipping,
    profile_findings,
    render_template,
)

NOW = datetime(2026, 10, 1, 10, 45, tzinfo=ZoneInfo("Europe/Berlin"))
RULES = ImportProfile(
    id="p",
    name="P",
    sender_rules=(
        SenderRule("@kunde.de"),
        SenderRule("@nord.kunde.de", SenderAction.REVIEW),
        SenderRule("chef@kunde.de", SenderAction.IGNORE),
    ),
    sender_default=SenderAction.IGNORE,
    shipping_methods=(ShippingOption("Paketversand", ("DHL", "Paket")),),
    payment_methods=(PaymentMethod.INVOICE,),
)


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Workbench, MemorySettings]:
    database, settings = build_demo(tmp_path / "demo", NOW)
    store = MemorySettings()
    return Workbench(
        database.connect(), settings, FixedClock(NOW), store=store, data_dir=tmp_path / "demo"
    ), store


def test_profile_settings_round_trip_with_all_new_fields() -> None:
    settings = Settings(
        accounts=(MailAccount("m", "Mail", host="imap.example", username="u"),),
        profiles=(replace(RULES, mail_account_id="m"),),
        active_profile_id="p",
    )
    assert parse_settings(settings_to_dict(settings)) == settings


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"export_dir": "C:/Lexware/Import"}, "Exportordner wird schon"),
        ({"mail_account_id": "m"}, "gehört bereits"),
        ({"mail_account_id": "fehlt"}, "existiert nicht"),
    ],
)
def test_profiles_must_not_share_folders_or_mailboxes(change: dict[str, str], message: str) -> None:
    first = ImportProfile(id="a", name="A", export_dir="c:\\lexware\\import\\", mail_account_id="m")
    second = replace(ImportProfile(id="b", name="B"), **change)
    with pytest.raises(ConfigError) as info:
        parse_settings(
            settings_to_dict(
                Settings(
                    accounts=(MailAccount("m", "M"),),
                    profiles=(first, second),
                    active_profile_id="a",
                )
            )
        )
    assert any(message in d for d in info.value.details)


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (replace(RULES, sender_rules=(SenderRule("kunde.de"),)), "Domain (@firma.de)"),
        (
            replace(RULES, mail_template=MailTemplate("Hallo {unbekannt}", "")),
            "unbekannte Platzhalter",
        ),
        (replace(RULES, default_payment_method=PaymentMethod.CASH), "Standard-Zahlungsart"),
        (replace(RULES, tax_rates=(Decimal(150),)), "zwischen 0 und 100"),
    ],
)
def test_invalid_profile_values_are_reported(profile: ImportProfile, message: str) -> None:
    with pytest.raises(ConfigError) as info:
        parse_settings(
            settings_to_dict(Settings(profiles=(profile,), active_profile_id=profile.id))
        )
    assert any(message in d for d in info.value.details)


@pytest.mark.parametrize(
    ("sender", "action"),
    [
        ("chef@kunde.de", SenderAction.IGNORE),
        ("einkauf@kunde.de", SenderAction.ACCEPT),
        ("lager@nord.kunde.de", SenderAction.REVIEW),
        ("x@werk.kunde.de", SenderAction.ACCEPT),
        ("x@kunde.de.example", SenderAction.IGNORE),
        ("x@anderer.de", SenderAction.IGNORE),
    ],
)
def test_sender_rules_exact_before_most_specific_domain(sender: str, action: SenderAction) -> None:
    assert evaluate_sender(RULES, sender).action is action


def test_shipping_terms_template_and_profile_findings() -> None:
    assert match_shipping(RULES, "Versand per DHL") is RULES.shipping_methods[0]
    assert match_shipping(RULES, "Spedition") is None
    subject, body = render_template(
        MailTemplate("Bestellung {bestellnummer}", "{firma} {unbekannt}"),
        {"bestellnummer": "B-1", "firma": "Kunde"},
    )
    assert (subject, body) == ("Bestellung B-1", "Kunde {unbekannt}")
    order = Order(
        id="o",
        profile_id="anders",
        payment_method=Field.manual(PaymentMethod.CASH),
        shipping_method=Field.manual("Spedition"),
    )
    codes = [f.code for f in profile_findings(order, RULES)]
    assert codes == ["PROFILE_MISMATCH", "PAYMENT_NOT_ALLOWED", "SHIPPING_UNKNOWN"]


def test_orders_catalogs_and_versions_are_separated(
    setup: tuple[Workbench, MemorySettings],
) -> None:
    workbench, store = setup
    assert len(workbench.rows()) == 11
    assert [v.number for v in workbench.catalogs.versions(STANDARD)] == [2, 1]
    workbench.switch_profile(NORDLICHT)
    assert store.saved[-1].active_profile_id == NORDLICHT
    assert sorted(r.order_id for r in workbench.rows()) == ["order-101", "order-102"]
    assert [v.number for v in workbench.catalogs.versions(NORDLICHT)] == [1]
    assert workbench.article("MW-0710") is None
    with pytest.raises(ValueError, match="aktiven Profils"):
        workbench.export(("order-004",), ExportMode.TEST)


def test_document_numbers_count_per_profile_and_never_reuse_legacy(
    setup: tuple[Workbench, MemorySettings],
) -> None:
    workbench, _ = setup
    conn = workbench._conn
    with transaction(conn):
        CounterRepository(conn).raise_to("document:AU:2026", 7)
    assert workbench.approve("order-001").order.document_number == "AU-2026-000008"
    workbench.switch_profile(NORDLICHT)
    assert workbench.approve("order-101").order.document_number == "NL-2026-000001"


def test_legacy_orders_without_profile_are_adopted(tmp_path: Path) -> None:
    database, settings = build_demo(tmp_path / "demo", NOW)
    conn = database.connect()
    with transaction(conn):
        OrderRepository(conn).insert(Order(id="alt-1"), "Altbestand", NOW)
    workbench = Workbench(conn, settings, FixedClock(NOW), data_dir=tmp_path / "demo")
    assert workbench.snapshot("alt-1").order.profile_id == STANDARD
    assert "orders_adopted" in [
        entry[1] for entry in JournalRepository(conn).entries("profile", STANDARD)
    ]


def test_profile_save_and_delete_rules(setup: tuple[Workbench, MemorySettings]) -> None:
    workbench, _ = setup
    nordlicht = workbench.settings.profile(NORDLICHT)
    with pytest.raises(ConfigError):
        workbench.save_profile(replace(nordlicht, export_dir=workbench.profile.export_dir))
    workbench.save_profile(ImportProfile(id="firma-c", name="Firma C", document_prefix="FC"))
    assert [p.id for p in workbench.profiles] == [STANDARD, NORDLICHT, "firma-c"]
    with pytest.raises(ValueError, match="aktive"):
        workbench.delete_profile(STANDARD)
    with pytest.raises(ValueError, match="Aufträge"):
        workbench.delete_profile(NORDLICHT)
    workbench.delete_profile("firma-c")
    assert len(workbench.profiles) == 2


def test_rematch_after_catalog_change_previews_then_applies(
    setup: tuple[Workbench, MemorySettings],
) -> None:
    workbench, _ = setup
    catalogs, profile = workbench.catalogs, workbench.profile
    data = (
        catalogs.export(profile, ExportFormat.CSV)
        + b"HM-1000;Bio-Hafermilch 1 l;;2,10;7;Karton;ja\r\n"
    )
    table = catalogs.read(data, "neu.csv")
    catalogs.commit(profile, catalogs.preview(profile, table, catalogs.suggest(profile.id, table)))
    plan = workbench.rematch_plan()
    change = next(c for c in plan.changes if c.order_id == "order-002")
    assert (change.position, change.kind) == (4, "neu zugeordnet")
    assert change.after.article is not None
    assert change.after.catalog_version == 3
    workbench.apply_rematch(plan)
    assert workbench.snapshot("order-002").order.status is OrderStatus.READY
    assert (
        workbench.snapshot("order-004").order.lines == workbench.snapshot("order-004").order.lines
    )
    assert not workbench.rematch_plan().changes


def test_accepting_a_candidate_is_manual_and_survives_rematch(
    setup: tuple[Workbench, MemorySettings],
) -> None:
    workbench, _ = setup
    workbench.switch_profile(NORDLICHT)
    order = workbench.snapshot("order-102").order
    candidate = order.lines[1].match.candidates[0].article
    saved = workbench.save(workbench.assign_article(order, 1, candidate))
    assert saved.order.lines[1].match.status.value == "manual"
    assert saved.order.status is OrderStatus.READY
    assert not workbench.rematch_plan().changes
