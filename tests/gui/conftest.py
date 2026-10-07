"""Qt im Offscreen-Modus; ohne PySide6 werden die Oberflächentests übersprungen."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from icware_auftragsimport.app.demo import build_demo
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.gui.app import create_application
from icware_auftragsimport.gui.main_window import MainWindow
from icware_auftragsimport.infrastructure.clock import FixedClock

NOW = datetime(2026, 10, 1, 10, 45, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else create_application(["tests"])


@pytest.fixture
def workbench(tmp_path: Path) -> Workbench:
    database, settings = build_demo(tmp_path / "demo", NOW)
    return Workbench(database.connect(), settings, FixedClock(NOW))


@pytest.fixture
def window(qapp: QApplication, workbench: Workbench) -> Iterator[MainWindow]:
    main = MainWindow(workbench, demo=True, persist=False)
    main.resize(1536, 824)
    main.show()
    qapp.processEvents()
    yield main
    main.detail.dirty = False
    main.close()
