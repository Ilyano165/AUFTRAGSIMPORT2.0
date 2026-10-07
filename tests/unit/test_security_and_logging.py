from __future__ import annotations

import logging
import os
from pathlib import Path

import keyring
import keyring.backends.fail
import pytest

from icware_auftragsimport.domain.errors import CredentialError
from icware_auftragsimport.infrastructure.atomic import atomic_write_bytes
from icware_auftragsimport.infrastructure.logging_setup import (
    LOG_FILE_NAME,
    configure_logging,
    get_logger,
)
from icware_auftragsimport.infrastructure.paths import AppPaths
from icware_auftragsimport.security.credentials import (
    KeyringCredentialStore,
    MemoryCredentialStore,
    SecretSource,
    credential_key,
    env_variable,
    resolve_secret,
)
from icware_auftragsimport.security.redaction import redact, redact_mapping


@pytest.mark.parametrize(
    ("raw", "hidden"),
    [
        ("login password=geheim123 ok", "geheim123"),
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
        ("IBAN DE89 3704 0044 0532 0130 00 bitte", "3704"),
        ("IBAN DE89370400440532013000", "DE89370400440532013000"),
        ("von einkauf@kunde.de", "einkauf@kunde.de"),
        ("Tel. 02292/921228", "921228"),
        ("Mobil +49 171 1234567", "1234567"),
        ("Karte 4111 1111 1111 1111", "1111 1111"),
    ],
)
def test_redaction_hides_sensitive_values(raw: str, hidden: str) -> None:
    assert hidden not in redact(raw)


@pytest.mark.parametrize(
    "safe",
    ["Beleg AI-2026-000123 exportiert", "Bestellnummer 251843", "Datum 30.09.2026", "PLZ 50667"],
)
def test_redaction_keeps_business_identifiers(safe: str) -> None:
    assert redact(safe) == safe


def test_redact_mapping_masks_sensitive_keys() -> None:
    result = redact_mapping({"username": "max", "port": 993, "nested": {"email": "a@b.de"}})
    assert result == {"username": "<entfernt>", "port": 993, "nested": {"email": "<entfernt>"}}


def test_environment_secret_needs_explicit_permission() -> None:
    store = MemoryCredentialStore()
    store.set(credential_key("vertrieb"), "aus-speicher")
    environ = {env_variable("vertrieb"): "aus-umgebung"}
    denied = resolve_secret(store, "vertrieb", allow_environment=False, environ=environ)
    allowed = resolve_secret(store, "vertrieb", allow_environment=True, environ=environ)
    assert (denied.value, denied.source) == ("aus-speicher", SecretSource.STORE)
    assert (allowed.value, allowed.source) == ("aus-umgebung", SecretSource.ENVIRONMENT)
    assert "aus-umgebung" not in repr(allowed)


def test_missing_secret_is_reported_as_none() -> None:
    result = resolve_secret(MemoryCredentialStore(), "x", allow_environment=False, environ={})
    assert result.source is SecretSource.NONE and result.value is None


def test_unusable_keyring_backend_fails_loudly() -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(keyring.backends.fail.Keyring())
    try:
        with pytest.raises(CredentialError) as info:
            KeyringCredentialStore()
    finally:
        keyring.set_keyring(previous)
    assert "kein Passwort gespeichert" in str(info.value)


def test_credential_key_uses_product_namespace() -> None:
    assert credential_key("vertrieb") == "IC-Ware/Auftrags-Import/vertrieb"
    assert env_variable("lager-nord") == "ICW_SECRET_LAGER_NORD"


def test_log_file_is_redacted_including_tracebacks(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path, "DEBUG")
    configure_logging(tmp_path, "DEBUG")
    assert len([h for h in logger.handlers if getattr(h, "_icware_handler", False)]) == 1
    log = get_logger("test")
    log.info("Anmeldung mit password=geheim fuer einkauf@kunde.de")
    try:
        raise ValueError("IBAN DE89370400440532013000")
    except ValueError:
        log.exception("Fehler")
    for handler in logger.handlers:
        handler.flush()
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "geheim" not in content and "einkauf@kunde.de" not in content
    assert "DE89370400440532013000" not in content
    assert "<IBAN>" in content
    logging.getLogger("icware").handlers.clear()


def test_atomic_write_keeps_old_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ziel.xml"
    target.write_bytes(b"alt")

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("Datenträger voll")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"neu")
    assert target.read_bytes() == b"alt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ziel.xml"]


def test_atomic_write_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "ziel.xml"
    atomic_write_bytes(target, b"eins")
    atomic_write_bytes(target, b"zwei")
    assert target.read_bytes() == b"zwei"


def test_default_paths_per_platform() -> None:
    windows = AppPaths.default(
        {"USERPROFILE": "C:/Users/a", "APPDATA": "C:/Users/a/AppData/Roaming"}, "win32"
    )
    linux = AppPaths.default({"HOME": "/home/a", "XDG_DATA_HOME": "/home/a/.local/share"}, "linux")
    assert windows.config_dir.as_posix().endswith("Roaming/IC-Ware/Auftrags-Import")
    assert windows.root.as_posix() == "C:/Users/a/AppData/Local/IC-Ware/Auftrags-Import"
    assert linux.root.as_posix() == "/home/a/.local/share/IC-Ware/Auftrags-Import"
    assert linux.config_dir.as_posix() == "/home/a/.config/IC-Ware/Auftrags-Import"
