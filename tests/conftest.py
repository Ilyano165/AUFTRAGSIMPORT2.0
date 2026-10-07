"""Gemeinsame Testhilfen. Kein Test berührt echte Benutzerdaten oder Anmeldespeicher."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.paths import AppPaths

FIXED_START = datetime(2026, 9, 30, 8, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Erzwingt isolierte Pfade und einen flüchtigen Anmeldespeicher, auch unter Windows."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    for name in list(__import__("os").environ):
        if name.startswith("ICW_SECRET_"):
            monkeypatch.delenv(name)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(FIXED_START)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    app_paths = AppPaths(root=tmp_path / "data")
    app_paths.ensure()
    return app_paths
