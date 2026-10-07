from __future__ import annotations

import json
from pathlib import Path

import pytest

from icware_auftragsimport.config import store as store_module
from icware_auftragsimport.config.schema import (
    ImportProfile,
    MailAccount,
    Settings,
    parse_settings,
    settings_to_dict,
)
from icware_auftragsimport.config.store import ConfigStore
from icware_auftragsimport.domain.errors import ConfigError
from icware_auftragsimport.domain.models import Address, PaymentMethod
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.paths import AppPaths


def _store(paths: AppPaths, clock: FixedClock) -> ConfigStore:
    return ConfigStore(paths.config_file, paths.backup_dir, clock)


def _sample() -> Settings:
    return Settings(
        accounts=(
            MailAccount(
                id="vertrieb",
                name="Vertrieb",
                host="imap.example.de",
                username="bestellung@example.de",
                allowed_senders=("@kunde.de", "einkauf@partner.at"),
            ),
        ),
        profiles=(
            ImportProfile(
                id="standard",
                name="Lexware",
                export_dir="C:/Lexware/Import",
                test_export_dir="C:/Lexware/Test",
                default_payment_method=PaymentMethod.INVOICE,
                supplier=Address(company="Beispiel GmbH", country="DE"),
            ),
        ),
    )


def test_missing_file_gives_defaults(paths: AppPaths, clock: FixedClock) -> None:
    settings = _store(paths, clock).load()
    assert settings.profile().id == "standard"
    assert settings.accounts == ()


def test_roundtrip(paths: AppPaths, clock: FixedClock) -> None:
    store = _store(paths, clock)
    store.save(_sample())
    assert store.load() == _sample()


def test_password_in_file_is_rejected_with_guidance() -> None:
    data = settings_to_dict(_sample())
    data["accounts"][0]["password"] = "geheim"
    with pytest.raises(ConfigError) as info:
        parse_settings(data)
    assert "Anmeldeinformationsverwaltung" in str(info.value)
    assert "geheim" not in str(info.value)


def test_all_problems_are_reported_at_once() -> None:
    data = settings_to_dict(_sample())
    data["accounts"][0]["port"] = 70000
    data["accounts"][0]["id"] = "Vertrieb Nord"
    data["accounts"][0]["use_ssl"] = False
    data["general"]["log_level"] = "LOUD"
    with pytest.raises(ConfigError) as info:
        parse_settings(data)
    details = info.value.details
    assert len(details) == 4
    assert any("accounts[0].port" in d for d in details)
    assert any("use_ssl: unbekannter Eintrag" in d for d in details)
    assert "bisher gültigen Einstellungen bleiben unverändert" in str(info.value)


def test_cross_field_rules() -> None:
    data = settings_to_dict(_sample())
    data["profiles"][0]["test_export_dir"] = "c:/lexware/import"
    data["active_profile_id"] = "fehlt"
    data["accounts"].append(dict(data["accounts"][0]))
    with pytest.raises(ConfigError) as info:
        parse_settings(data)
    text = " ".join(info.value.details)
    assert "darf nicht gleich dem Exportordner sein" in text
    assert "kein vorhandenes Profil" in text
    assert "mehrfach" in text


def test_broken_json_names_the_position(paths: AppPaths, clock: FixedClock) -> None:
    paths.config_file.write_text('{"general": {', encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        _store(paths, clock).load()
    assert info.value.code == "CONFIG_NOT_JSON"
    assert "Zeile 1" in str(info.value)


def test_invalid_import_leaves_settings_untouched(
    paths: AppPaths, clock: FixedClock, tmp_path: Path
) -> None:
    store = _store(paths, clock)
    store.save(_sample())
    before = paths.config_file.read_bytes()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"profiles": "keine Liste"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        store.import_from(bad)
    assert paths.config_file.read_bytes() == before


def test_export_and_import_roundtrip(paths: AppPaths, clock: FixedClock, tmp_path: Path) -> None:
    store = _store(paths, clock)
    exported = tmp_path / "export.json"
    store.export_to(exported, _sample())
    assert store.import_from(exported) == _sample()


def test_backups_are_created_and_pruned(
    paths: AppPaths, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MAX_BACKUPS", 3)
    store = _store(paths, clock)
    for _ in range(6):
        store.save(_sample())
        clock.advance(1)
    backups = store.list_backups()
    assert len(backups) == 3
    restored = store.restore(backups[-1])
    assert restored == _sample()


def test_export_encoding_and_target_validation_rules() -> None:
    data = settings_to_dict(_sample())
    data["profiles"][0]["export_encoding"] = "CP850"
    data["profiles"][0]["target_system"] = "Lexware warenwirtschaft pro"
    with pytest.raises(ConfigError) as info:
        parse_settings(data)
    text = " ".join(info.value.details)
    assert "export_encoding" in text and "gemeinsam gesetzt" in text
    data["profiles"][0]["export_encoding"] = "iso-8859-1"
    data["profiles"][0]["target_validated_on"] = "2026-10-15"
    profile = parse_settings(data).profile()
    assert profile.export_encoding == "ISO-8859-1" and profile.target_validated
