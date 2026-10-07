"""Vertragstests für Installer, PyInstaller-Spezifikation, Sperrdateien und Pipeline.

Sie schützen Entscheidungen, deren Verletzung erst beim Kunden auffallen würde.
"""

from __future__ import annotations

import re
from pathlib import Path

from icware_auftragsimport.gui.app import APP_USER_MODEL_ID

ROOT = Path(__file__).resolve().parents[2]
WXS = (ROOT / "packaging" / "wix" / "AuftragsImport.wxs").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "pyinstaller" / "AuftragsImport.spec").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
UPGRADE_CODE = "7C2F5A31-4E8B-4C1D-9A55-2D1E8F3B6A90"


def test_installer_keeps_upgrade_identity_and_never_touches_user_data() -> None:
    assert f'UpgradeCode="{UPGRADE_CODE}"' in WXS, "UpgradeCode nie ändern"
    assert '<MajorUpgrade AllowSameVersionUpgrades="yes"' in WXS
    assert 'Scope="perMachine"' in WXS and "ProgramFiles64Folder" in WXS
    for user_location in (
        "AppDataFolder",
        "LocalAppDataFolder",
        "CommonAppDataFolder",
        "RemoveFolderEx",
        "RemoveFile",
    ):
        assert user_location not in WXS
    assert f'Value="{APP_USER_MODEL_ID}"' in WXS
    assert 'Language="1031"' in WXS


def test_spec_builds_onedir_without_upx_or_console() -> None:
    assert "COLLECT(" in SPEC and "exclude_binaries=True" in SPEC
    assert (
        "upx=False" in SPEC
        and "console=False" in SPEC
        and "disable_windowed_traceback=True" in SPEC
    )
    assert '"_de"' in SPEC


def test_locks_pin_every_package_with_hashes_and_exclude_webengine() -> None:
    for lock in (ROOT / "requirements").glob("*.lock"):
        text = lock.read_text(encoding="utf-8")
        pins = re.findall(
            r"^([a-z0-9][a-z0-9._-]*)==\S+ \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)",
            text,
            re.M,
        )
        assert pins, lock.name
        assert len(pins) == len(re.findall(r"^[a-z0-9][a-z0-9._-]*==", text, re.M)), lock.name
    build = (ROOT / "requirements" / "build-windows.lock").read_text(encoding="utf-8")
    assert "pyside6-addons" not in build and "pyside6-essentials==6.11.2" in build


def test_pipeline_uses_hashes_pinned_python_and_signs_before_packaging() -> None:
    assert "--require-hashes" in WORKFLOW and "python-version-file: .python-version" in WORKFLOW
    assert (
        WORKFLOW.index("EXE signieren")
        < WORKFLOW.index("build.py msi")
        < WORKFLOW.index("Installer signieren")
    )
    assert "pip_audit" in WORKFLOW and "build.py compare" in WORKFLOW
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip().startswith("3.14.")


def test_generated_files_are_not_versioned() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("build/", "dist/", "_build_info.py"):
        assert entry in ignored
