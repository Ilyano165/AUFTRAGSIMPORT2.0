"""Release-Vorbereitung: Versionen, Build-Infos, Ablageorte, Richtlinie, Updates, Fehlerberichte."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest

from icware_auftragsimport import buildinfo
from icware_auftragsimport.buildinfo import BuildInfo, render_module
from icware_auftragsimport.config.policy import load_policy, parse_policy
from icware_auftragsimport.infrastructure.paths import AppPaths, migrate_legacy_layout
from icware_auftragsimport.security import crash
from icware_auftragsimport.security.redaction import anonymize_paths, register_secret
from icware_auftragsimport.services.updates import (
    ManifestError,
    UpdateState,
    evaluate,
    parse_manifest,
)
from icware_auftragsimport.versioning import Version, VersionError

ROOT = Path(__file__).resolve().parents[2]


def _tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"release_{name}", ROOT / "tools" / "release" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_semver_precedence_follows_the_specification() -> None:
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
        "2.0.0",
        "2.0.1",
        "2.1.0",
    ]
    parsed = [Version.parse(v) for v in ordered]
    assert sorted(reversed(parsed)) == parsed
    assert str(Version.parse("2.1.0-rc.1+build.7")) == "2.1.0-rc.1+build.7"
    for invalid in ("2.0", "02.0.0", "2.0.0-", "v2.0.0", "2.0.0.1"):
        with pytest.raises(VersionError):
            Version.parse(invalid)


def test_bump_and_windows_versions() -> None:
    version = Version.parse("2.0.0")
    assert [str(version.bump(p)) for p in ("patch", "minor", "major")] == [
        "2.0.1",
        "2.1.0",
        "3.0.0",
    ]
    assert str(Version.parse("2.1.0-rc.2").bump("patch")) == "2.1.0"
    assert Version.parse("2.0.1").msi_version() == "2.0.1"
    assert Version.parse("2.0.1").file_version(57) == (2, 0, 1, 57)
    with pytest.raises(VersionError, match="255"):
        Version.parse("256.0.0").msi_version()
    with pytest.raises(VersionError):
        Version.parse("2.0.1").file_version(70_000)


def test_version_tool_groups_unreleased_sections(tmp_path: Path) -> None:
    tool = _tool("version")
    init, log = tmp_path / "__init__.py", tmp_path / "CHANGELOG.md"
    init.write_text('__version__ = "2.0.0"\n', encoding="utf-8")
    log.write_text(
        "# Änderungen\n\n## [Unveröffentlicht] – Stammdaten\n\n- a\n\n"
        "## [Unveröffentlicht] – Oberfläche\n\n- b\n\n"
        "## [1.1.0] – 2026-01-01\n",
        encoding="utf-8",
    )
    tool.write(Version.parse("2.0.1"), init=init, changelog=log, today=date(2026, 10, 2))
    text = log.read_text(encoding="utf-8")
    assert '__version__ = "2.0.1"' in init.read_text(encoding="utf-8")
    assert "## [2.0.1] – 2026-10-02\n\n### Stammdaten" in text
    assert "### Oberfläche" in text
    assert "Unveröffentlicht" not in text


def test_build_info_module_round_trip_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    info = BuildInfo("2.0.1", "57", "a" * 40, datetime(2026, 10, 1, 8, 45, tzinfo=UTC))
    namespace: dict[str, object] = {}
    exec(render_module(info), namespace)
    module = ModuleType("icware_auftragsimport._build_info")
    module.__dict__.update(namespace)
    monkeypatch.setitem(sys.modules, "icware_auftragsimport._build_info", module)
    assert buildinfo.current() == info
    assert info.describe() == "2.0.1 · Build 57 · aaaaaaaaaa · 2026-10-01 08:45 UTC"
    assert info.is_release
    monkeypatch.setitem(sys.modules, "icware_auftragsimport._build_info", None)
    dev = buildinfo.current()
    assert (dev.build, dev.commit, dev.is_release) == ("lokal", "unversioniert", False)
    assert dev.short_commit == "unversioniert"


def test_windows_layout_separates_program_settings_and_data() -> None:
    env = {
        "APPDATA": r"C:\Users\Max\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\Max\AppData\Local",
        "PROGRAMDATA": r"C:\ProgramData",
    }
    paths = AppPaths.default(env, "win32")
    assert (
        str(paths.config_file)
        .replace("\\", "/")
        .endswith("Roaming/IC-Ware/Auftrags-Import/settings.json")
    )
    for local in (
        paths.database_file,
        paths.log_dir,
        paths.crash_dir,
        paths.catalog_backup_dir,
        paths.lock_file,
    ):
        assert "AppData/Local/IC-Ware/Auftrags-Import" in str(local).replace("\\", "/")
    assert (
        str(paths.policy_file).replace("\\", "/")
        == "C:/ProgramData/IC-Ware/Auftrags-Import/policy.json"
    )


def test_legacy_roaming_database_moves_to_local_once(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "local", config_root=tmp_path / "roaming")
    paths.config_dir.mkdir(parents=True)
    for name in ("auftragsimport.db", "auftragsimport.db-wal"):
        (paths.config_dir / name).write_text(name, encoding="utf-8")
    (paths.config_dir / "katalog-backups").mkdir()
    (paths.config_dir / "settings.json").write_text("{}", encoding="utf-8")
    assert migrate_legacy_layout(paths) == [
        "auftragsimport.db",
        "auftragsimport.db-wal",
        "katalog-backups",
    ]
    assert paths.database_file.read_text(encoding="utf-8") == "auftragsimport.db"
    assert paths.config_file.exists()
    (paths.config_dir / "auftragsimport.db").write_text("neu", encoding="utf-8")
    assert migrate_legacy_layout(paths) == []
    assert paths.database_file.read_text(encoding="utf-8") == "auftragsimport.db"


def test_machine_policy_defaults_validation_and_no_secrets(tmp_path: Path) -> None:
    policy, issues = load_policy(tmp_path / "fehlt.json")
    assert (issues, policy.updates.check_enabled) == ([], False)
    policy, issues = parse_policy(
        {
            "support_contact": "IT 0221 1",
            "log_level": "DEBUG",
            "updates": {"check_enabled": True, "manifest_url": "https://ic-ware.eu/u.json"},
        }
    )
    assert (policy.support_contact, policy.log_level, policy.updates.check_enabled, issues) == (
        "IT 0221 1",
        "DEBUG",
        True,
        [],
    )
    policy, issues = parse_policy({"updates": {"manifest_url": "http://x"}, "farbe": "rot"})
    assert any("https" in i for i in issues) and any("farbe" in i for i in issues)
    assert not policy.updates.check_enabled
    _, issues = parse_policy({"imap": {"password": "geheim"}})
    assert issues == ["imap.password: Geheimnisse gehören nicht in die Richtlinie"]
    (tmp_path / "policy.json").write_text("{kaputt", encoding="utf-8")
    assert load_policy(tmp_path / "policy.json")[1][0].startswith("policy.json: nicht lesbar")


MANIFEST = {
    "format": "icware-update-manifest",
    "channel": "stable",
    "version": "2.0.2",
    "released": "2026-11-02",
    "minimum_version": "2.0.0",
    "notes_url": "https://ic-ware.eu/auftrags-import/2.0.2",
    "download_page": "https://downloads.ic-ware.eu/auftrags-import",
    "msi": {
        "name": "IC-Ware-AuftragsImport-2.0.2-x64.msi",
        "sha256": "ab" * 32,
        "size": 80_000_000,
    },
}


def test_update_manifest_is_strict_and_comparison_is_semver() -> None:
    manifest = parse_manifest(json.dumps(MANIFEST).encode())
    assert evaluate(manifest, Version.parse("2.0.1"), "stable") is UpdateState.AVAILABLE
    assert evaluate(manifest, Version.parse("2.0.2"), "stable") is UpdateState.CURRENT
    assert evaluate(manifest, Version.parse("1.9.0"), "stable") is UpdateState.REQUIRED
    for change in (
        {"notes_url": "http://ic-ware.eu/x"},
        {"download_page": "https://evil.example/x"},
        {"msi": {**MANIFEST["msi"], "sha256": "xyz"}},
        {"msi": {**MANIFEST["msi"], "name": "a.exe"}},
        {"version": "2.0"},
        {"format": "anders"},
    ):
        with pytest.raises(ManifestError):
            parse_manifest(json.dumps({**MANIFEST, **change}).encode())
    beta = parse_manifest(
        json.dumps({**MANIFEST, "channel": "beta", "version": "2.1.0-rc.1"}).encode()
    )
    assert evaluate(beta, Version.parse("2.0.2"), "stable") is UpdateState.CURRENT
    assert evaluate(beta, Version.parse("2.0.2"), "beta") is UpdateState.AVAILABLE


def test_crash_report_has_id_build_and_no_secrets_or_user_paths(tmp_path: Path) -> None:
    register_secret("Sommer2026!")
    try:
        raise ValueError(f"IMAP Login Sommer2026! in {Path.home()}/daten")
    except ValueError as exc:
        record = crash.write_crash_report(
            tmp_path, type(exc), exc, exc.__traceback__, build="2.0.1 · Build 57"
        )
    assert re.fullmatch(r"E-\d{6}-[2-9A-HJ-NP-Z]{6}", record.error_id)
    assert record.report is not None
    text = record.report.read_text(encoding="utf-8")
    assert text.startswith(f"Fehler-ID: {record.error_id}\nZeitpunkt: ")
    assert "Build: 2.0.1 · Build 57" in text
    assert "Sommer2026!" not in text and str(Path.home()) not in text
    assert (
        anonymize_paths(r"C:\Users\max\x.py", home=r"C:\Users\max", user="max")
        == r"%USERPROFILE%\x.py"
    )


def test_crash_handler_notifies_for_threads_and_survives_failing_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crash, "KEEP_REPORTS", 3)
    seen: list[crash.CrashRecord] = []
    previous = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    try:
        crash.install_crash_handler(tmp_path, build="test", notify=seen.append, native=False)
        for _ in range(4):
            sys.excepthook(RuntimeError, RuntimeError("x"), None)
        worker = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError("im Thread")))
        worker.start()
        worker.join()

        def broken(_record: crash.CrashRecord) -> None:
            raise OSError("kein Bildschirm")

        crash.install_crash_handler(tmp_path, notify=broken, native=False)
        sys.excepthook(RuntimeError, RuntimeError("y"), None)
    finally:
        sys.excepthook, threading.excepthook, sys.unraisablehook = previous
    assert len(seen) == 5
    assert len(list(tmp_path.glob("absturz-*.txt"))) == 3


def test_version_resource_and_tree_comparison(tmp_path: Path) -> None:
    build = _tool("build")
    resource = build.version_resource(Version.parse("2.0.1"), 57, 2026)
    assert (
        "filevers=(2, 0, 1, 57)" in resource
        and "'040704B0'" in resource
        and "[1031, 1200]" in resource
    )
    assert "StringStruct('ProductVersion', '2.0.1')" in resource
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        (folder / "lib").mkdir(parents=True)
        (folder / "lib" / "x.dll").write_bytes(b"gleich")
    assert build.compare(first, second) == 0
    (second / "lib" / "x.dll").write_bytes(b"anders")
    assert build.compare(first, second) == 1
