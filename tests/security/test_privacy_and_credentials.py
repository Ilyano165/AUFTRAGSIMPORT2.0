"""Datenschutz und Geheimnisse: Redaktion, Protokoll, Absturzberichte, Diagnosebericht."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from icware_auftragsimport.config.schema import (
    MailAccount,
    Settings,
    parse_settings,
    settings_to_dict,
)
from icware_auftragsimport.domain.errors import ConfigError
from icware_auftragsimport.infrastructure.db import Database, transaction
from icware_auftragsimport.infrastructure.logging_setup import configure_logging, get_logger
from icware_auftragsimport.infrastructure.repositories import JournalRepository, OrderRepository
from icware_auftragsimport.security.crash import crash_report, install_crash_handler
from icware_auftragsimport.security.credentials import (
    MemoryCredentialStore,
    credential_key,
    env_variable,
    resolve_secret,
)
from icware_auftragsimport.security.redaction import redact, register_secret
from icware_auftragsimport.security.runtime import check_runtime
from icware_auftragsimport.services.diagnostics import build_support_report
from support import EXPORT_PROFILE, approved_order

SECRET = "Sup3r-Geheim!2026"
PII = (
    "t.mueller@muster.de",
    "0221 123456",
    "+49 221 123456",
    "DE89 3704 0044 0532 0130 00",
    "1234567890",
    "37040044",
    "Musterweg 12a",
    "Industriestr. 5-7",
    "50667 Köln",
    "Postfach 1234",
    "4111 1111 1111 1111",
)


def test_redaction_covers_contact_bank_and_address_data() -> None:
    text = (
        "Kontakt t.mueller@muster.de, Tel. 0221 123456 oder +49 221 123456, "
        "IBAN DE89 3704 0044 0532 0130 00, "
        "Kontonummer: 1234567890, BLZ 37040044, Lieferung Musterweg 12a / Industriestr. 5-7, "
        "50667 Köln, Postfach 1234, Karte 4111 1111 1111 1111"
    )
    cleaned = redact(text)
    assert [item for item in PII if item in cleaned] == []


def test_logs_never_contain_secrets_and_are_bounded(tmp_path: Path) -> None:
    register_secret(SECRET)
    configure_logging(tmp_path)
    log = get_logger("test")
    log.error("Anmeldung mit %s für t.mueller@muster.de fehlgeschlagen", SECRET)
    log.info("Mailinhalt: %s", "Bestellung " * 5000)
    try:
        raise RuntimeError(f"password={SECRET}")
    except RuntimeError:
        log.exception("Absturz")
    for handler in logging.getLogger("icware").handlers:
        handler.flush()
    content = (tmp_path / "auftrags-import.log").read_text(encoding="utf-8")
    assert SECRET not in content and "t.mueller@muster.de" not in content
    assert max(len(line) for line in content.splitlines()) < 4_100


def test_crash_reports_have_no_secrets_and_no_local_variables(tmp_path: Path) -> None:
    register_secret(SECRET)

    def failing() -> None:
        iban_local = "DE89370400440532013000"  # noqa: F841
        raise ValueError(f"Login {SECRET} für t.mueller@muster.de")

    try:
        failing()
    except ValueError as exc:
        report = crash_report(type(exc), exc, exc.__traceback__)
    assert SECRET not in report and "t.mueller@muster.de" not in report and "DE8937" not in report
    previous = sys.excepthook
    try:
        install_crash_handler(tmp_path)
        try:
            failing()
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        sys.excepthook = previous
    [written] = tmp_path.glob("absturz-*.txt")
    assert SECRET not in written.read_text(encoding="utf-8")


def test_environment_fallback_only_when_explicitly_allowed() -> None:
    store = MemoryCredentialStore()
    store.set(credential_key("einkauf"), "aus-speicher")
    env = {env_variable("einkauf"): "aus-umgebung"}
    assert (
        resolve_secret(store, "einkauf", allow_environment=False, environ=env).value
        == "aus-speicher"
    )
    assert (
        resolve_secret(store, "einkauf", allow_environment=True, environ=env).value
        == "aus-umgebung"
    )
    assert "aus-speicher" not in repr(
        resolve_secret(store, "einkauf", allow_environment=False, environ=env)
    )


def test_support_report_contains_no_order_or_personal_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "d.db")
    conn = database.connect()
    database.migrate(conn)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    with transaction(conn):
        OrderRepository(conn).insert(approved_order(note="Bitte an Frau Müller"), "Test", now)
        JournalRepository(conn).append(
            "order", "order-0001", "export_success", now, {"file": "C:/Lexware/AI-2026-000123.xml"}
        )
    account = MailAccount(
        id="einkauf",
        name="Einkauf Müller",
        host="imap.kunde-geheim.de",
        username="t.mueller@muster.de",
    )
    profile = replace(
        EXPORT_PROFILE,
        export_dir="C:/Users/tmueller/Lexware",
        test_export_dir="C:/Users/tmueller/Test",
    )
    report = json.dumps(
        build_support_report(conn, Settings(accounts=(account,), profiles=(profile,)), now=now),
        ensure_ascii=False,
    )
    forbidden = (
        "Müller",
        "muster.de",
        "PO-4711",
        "AI-2026",
        "Musterweg",
        "50667",
        "Köln",
        "YT11YBOR01",
        "imap.kunde-geheim.de",
        "tmueller",
        "Lexware/",
        "Yellotools",
        "Windeck",
    )
    assert [item for item in forbidden if item in report] == []
    assert '"orders_by_status": {"approved": 1}' in report


def test_runtime_check_flags_known_vulnerable_versions() -> None:
    def strict(addresses: list[str], *, strict: bool = True) -> list[tuple[str, str]]:
        return []

    old = check_runtime((3, 12, 3), (2, 6, 1), (3, 0, 13), getaddresses=strict)
    assert len(old.problems) == 2 and not old.secure
    assert any("CVE-2024-8176" in p for p in old.problems)
    assert check_runtime((3, 12, 14), (2, 8, 3), (3, 0, 13), getaddresses=strict).secure

    def legacy(addresses: list[str]) -> list[tuple[str, str]]:
        return []

    assert not check_runtime((3, 12, 14), (2, 8, 3), (3, 0, 13), getaddresses=legacy).secure


@pytest.mark.parametrize("key", ["password", "Passwort", "token", "secret"])
def test_configuration_never_accepts_secrets(key: str) -> None:
    data = settings_to_dict(Settings())
    data["general"][key] = "x"
    with pytest.raises(ConfigError):
        parse_settings(data)
