"""Produktprüfung des Programmordners und Selbsttest des fertigen Programms."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from icware_auftragsimport.domain.errors import CredentialError, UserMessage
from icware_auftragsimport.gui.app import credential_check, fetch_check, tls_check
from icware_auftragsimport.security.credentials import MemoryCredentialStore

ROOT = Path(__file__).resolve().parents[2]


def _audit() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_audit", ROOT / "tools" / "release" / "audit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _audit()


# ---------- Produktprüfung ----------


def test_forbidden_modules_are_reported() -> None:
    names = [
        "icware_auftragsimport.app.testmails",
        "icware_auftragsimport.app.demo",
        "keyring.core",
        "tests.unit.test_x",
        "conftest",
        "_pytest.python",
        "fetchkit",
        "setuptools._distutils.core",
        "_distutils_hack",
        "unittest.mock",
    ]
    found = audit.module_problems(names)
    assert len(found) == 7
    assert not any("icware_auftragsimport" in p or "keyring" in p for p in found)


def test_absolute_paths_in_code_objects_are_reported() -> None:
    names = [
        ("ok", "icware_auftragsimport/ingest/imap.py"),
        ("linux", "/home/dev/src/icware_auftragsimport/x.py"),
        ("windows", "C:\\Users\\Ilyas\\repo\\src\\x.py"),
        ("marker", "lib/D:/a/repo/x.py"),
    ]
    found = audit.code_path_problems(names, ["D:/a/repo"])
    assert [p.split(" in ")[1].split(":")[0] for p in found] == ["linux", "windows", "marker"]


def test_files_in_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "AuftragsImport"
    (bundle / "_internal" / "icware_auftragsimport").mkdir(parents=True)
    (bundle / "docs").mkdir()
    (bundle / "AuftragsImport.exe").write_bytes(b"MZ")
    (bundle / "docs" / "Richtlinie-Beispiel.json").write_text('{"x": 1}')
    (bundle / "docs" / "LIZENZ.txt").write_text("Lizenz")
    (bundle / "_internal" / "icware_auftragsimport" / "spec.json").write_text("{}")
    assert audit.file_problems(bundle, ["/build/root"]) == []

    (bundle / ".env").write_text("ICW_SECRET_X=geheim")
    (bundle / "_internal" / "demo.db").write_bytes(b"SQLite")
    (bundle / "settings.json").write_text("{}")
    (bundle / "policy.json").write_text("{}")
    (bundle / "zert.key").write_text("x")
    (bundle / "tests").mkdir()
    (bundle / ".git").mkdir()
    (bundle / "docs" / "ca.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n")
    (bundle / "docs" / "info.json").write_text('{"pfad": "/build/root/src"}')
    found = audit.file_problems(bundle, ["/build/root"])
    assert len(found) == 9
    assert any(p == "Privater Schlüssel in docs/ca.pem" for p in found)
    assert any(p.startswith("Build-Pfad in docs/info.json") for p in found)


# ---------- Selbsttest-Prüfungen ----------


def _broken() -> MemoryCredentialStore:
    raise CredentialError(
        "CREDENTIAL_UNAVAILABLE",
        UserMessage(what="x", why="Kein nutzbarer Speicher", unchanged="-", action="-"),
    )


class _Forgetful(MemoryCredentialStore):
    def set(self, key: str, secret: str) -> None:
        return None


def test_credential_check_round_trip_leaves_nothing_behind() -> None:
    store = MemoryCredentialStore()
    name, ok, detail = credential_check(lambda: store, windows=True)
    assert (name, ok) == ("Anmeldespeicher", True)
    assert detail == "MemoryCredentialStore: speichern, lesen, löschen"
    assert store._items == {}


def test_credential_check_missing_store_fails_on_windows_only() -> None:
    assert credential_check(_broken, windows=True) == (
        "Anmeldespeicher",
        False,
        "Kein nutzbarer Speicher",
    )
    assert credential_check(_broken, windows=False)[1] is None


def test_credential_check_detects_store_that_keeps_nothing() -> None:
    assert credential_check(_Forgetful, windows=True)[1] is False


def test_tls_check_finds_system_roots() -> None:
    name, ok, detail = tls_check()
    assert name == "TLS-Zertifikatsspeicher" and ok and detail.endswith("Stammzertifikate")


def test_fetch_check_runs_the_complete_pipeline(tmp_path: Path) -> None:
    _name, ok, detail = fetch_check(tmp_path / "abruf")
    assert ok and detail == "8 geprüft, 4 Aufträge, 1 Spam"


def test_self_test_command_reports_every_check(tmp_path: Path) -> None:
    report = tmp_path / "selbsttest.txt"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT / "src")}
    if sys.platform == "win32":
        # Wie die fertige EXE: echter Anmeldespeicher (nur ein Testeintrag, der wieder gelöscht
        # wird) statt des flüchtigen Speichers aus conftest.py.
        env.pop("PYTHON_KEYRING_BACKEND", None)
    result = subprocess.run(
        [sys.executable, "-m", "icware_auftragsimport", "--self-test", "--output", str(report)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert report.exists(), result.stderr.decode(errors="replace")[-2000:]
    lines = report.read_text(encoding="utf-8").splitlines()
    checks = {line[7:].split(":")[0]: line[:6].strip() for line in lines}
    expected = {
        "Laufzeit",
        "Lexware-Spezifikation",
        "Datenbank",
        "Anmeldespeicher",
        "TLS-Zertifikatsspeicher",
        "Mail-Abruf (Testpostfach)",
        "Deutsche Qt-Texte",
        "Oberfläche",
    }
    assert set(checks) == expected
    assert checks["Mail-Abruf (Testpostfach)"] == "OK" and checks["Datenbank"] == "OK"
    windows = sys.platform == "win32"
    assert checks["Anmeldespeicher"] == ("OK" if windows else "–")
    failed = [name for name, mark in checks.items() if mark == "FEHLER"]
    assert result.returncode == (1 if failed else 0)


@pytest.mark.skipif(sys.platform != "win32", reason="NOT TESTED – WINDOWS REQUIRED")
def test_windows_credential_manager_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur unter Windows: echter Eintrag in der Anmeldeinformationsverwaltung."""
    import keyring.core  # noqa: PLC0415
    from keyring.backends import fail  # noqa: PLC0415

    # conftest.py erzwingt den Fail-Speicher; hier bewusst die echte Erkennung (Testeintrag wird
    # wieder gelöscht). keyring merkt sich das Backend prozessweit, daher danach zurücksetzen.
    monkeypatch.delenv("PYTHON_KEYRING_BACKEND", raising=False)
    keyring.core.init_backend()
    try:
        _name, ok, detail = credential_check()
    finally:
        keyring.core.set_keyring(fail.Keyring())
    assert ok, detail
    assert detail.startswith("WinVaultKeyring")


# ---------- Kontrollierter Testfehler (--fehlertest) ----------


def test_controlled_failure_report_hides_planted_secrets(tmp_path: Path) -> None:
    from icware_auftragsimport.gui.app import (  # noqa: PLC0415
        FAILURE_TEST_SECRET,
        ControlledTestError,
        controlled_failure,
    )
    from icware_auftragsimport.security import crash  # noqa: PLC0415

    with pytest.raises(ControlledTestError) as info:
        controlled_failure()
    record = crash.write_crash_report(
        tmp_path, info.type, info.value, info.tb, build="2.0.0 · Build 1"
    )
    assert record.report is not None
    text = record.report.read_text(encoding="utf-8")
    assert text.startswith(f"Fehler-ID: {record.error_id}\nZeitpunkt: ")
    assert "ControlledTestError" in text and "Kontrollierter Testfehler" in text
    assert FAILURE_TEST_SECRET not in text
    assert str(Path.home()) not in text
    assert "test.kunde@example.de" not in text


def test_failure_test_is_refused_in_demo_mode() -> None:
    from icware_auftragsimport.gui.app import main  # noqa: PLC0415

    with pytest.raises(SystemExit) as info:
        main(["--demo", "--fehlertest"])
    assert info.value.code == 2


# ---------- Abhängigkeiten sperren (tools/release/lock.py) ----------


def _lock() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_lock", ROOT / "tools" / "release" / "lock.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FAKE_INDEX = {
    "pip-audit": ("2.10.1", ["CacheControl[filecache]>=0.13", "pip-api>=0.0.28"]),
    "cachecontrol": (
        "0.14.4",
        [
            "msgpack<2,>=0.5.2",
            'filelock>=3.8.0; extra == "filecache"',
            'redis>=2.10.5; extra == "redis"',
        ],
    ),
    "keyring": (
        "25.7.0",
        [
            'pywin32-ctypes>=0.2.0; sys_platform == "win32"',
            'SecretStorage>=3.2; sys_platform == "linux"',
        ],
    ),
    "plain": ("1.0", ["CacheControl"]),
    "later": ("1.0", ["CacheControl[filecache]"]),
}


def _resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lines: str) -> set[str]:
    lock = _lock()
    from packaging.utils import canonicalize_name  # noqa: PLC0415

    def choose(name: str, _specifier: object) -> tuple[str, list[tuple[str, str]], list[str]]:
        version, requires = FAKE_INDEX.get(canonicalize_name(name), ("1.0", []))
        return version, [(f"{name}.whl", "ab" * 32)], requires

    monkeypatch.setattr(lock, "_choose", choose)
    source = tmp_path / "werkzeuge.in"
    source.write_text(lines, encoding="utf-8")
    return {canonicalize_name(name) for name, *_rest in lock.resolve(source)}


def test_lock_resolves_extras_requested_by_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    names = _resolve(monkeypatch, tmp_path, "pip-audit\n")
    assert {"cachecontrol", "filelock", "msgpack", "pip-api"} <= names
    assert "redis" not in names


def test_lock_adds_extras_requested_after_the_package_was_chosen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert "filelock" not in _resolve(monkeypatch, tmp_path, "plain\n")
    assert "filelock" in _resolve(monkeypatch, tmp_path, "plain\nlater\n")


def test_lock_evaluates_markers_for_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    names = _resolve(monkeypatch, tmp_path, "keyring\n")
    assert "pywin32-ctypes" in names and "secretstorage" not in names


def test_ci_tools_match_development_dependencies() -> None:
    import tomllib  # noqa: PLC0415

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = set(project["project"]["optional-dependencies"]["dev"])
    lines = (ROOT / "requirements" / "ci-tools.in").read_text(encoding="utf-8").splitlines()
    tools = {line.strip() for line in lines if line.strip() and not line.startswith("#")}
    assert tools == dev
    locked = (ROOT / "requirements" / "ci-tools.lock").read_text(encoding="utf-8")
    for pin in sorted(dev):
        name, version = pin.split("==")
        assert f"{name.lower()}=={version} \\" in locked, f"{pin} fehlt in ci-tools.lock"
