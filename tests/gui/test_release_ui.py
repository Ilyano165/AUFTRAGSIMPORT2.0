"""Fehlerdialog, Info-Dialog und deutsche Qt-Texte."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QLabel

from icware_auftragsimport.buildinfo import BuildInfo
from icware_auftragsimport.gui import crash_dialog
from icware_auftragsimport.gui.about import AboutDialog
from icware_auftragsimport.gui.crash_dialog import CrashDialog, CrashReporter
from icware_auftragsimport.gui.main_window import SHORTCUTS
from icware_auftragsimport.infrastructure.paths import AppPaths
from icware_auftragsimport.security.crash import CrashRecord

RECORD = CrashRecord("E-261002-7KQ2XM", datetime(2026, 10, 2, 9, 30, tzinfo=UTC), None)


def test_crash_dialog_shows_id_time_and_support_only(qapp: QApplication) -> None:
    dialog = CrashDialog(RECORD, "support@ic-ware.eu")
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "E-261002-7KQ2XM" in texts
    assert "02.10.2026" in texts
    assert "support@ic-ware.eu" in texts and "keine Passwörter" in texts
    assert "Traceback" not in texts and "Error" not in texts
    dialog.copy_id()
    assert QApplication.clipboard().text() == "E-261002-7KQ2XM"


def test_reporter_limits_dialogs(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        crash_dialog.CrashDialog, "exec", lambda self: shown.append(self.record.error_id) or 0
    )
    reporter = CrashReporter("support")
    for _ in range(5):
        reporter._show(RECORD)
    assert len(shown) == crash_dialog.MAX_DIALOGS_PER_WINDOW


def test_about_dialog_shows_build_and_locations(qapp: QApplication, tmp_path: Path) -> None:
    info = BuildInfo("2.0.1", "57", "0123456789abcdef", datetime(2026, 10, 1, 8, 45, tzinfo=UTC))
    paths = AppPaths(
        root=tmp_path / "local", config_root=tmp_path / "roaming", machine_root=tmp_path / "machine"
    )
    dialog = AboutDialog(paths, SHORTCUTS, info=info)
    assert dialog.values["Version"].text() == "2.0.1"
    assert dialog.values["Build"].text() == "57"
    assert dialog.values["Commit"].text() == "0123456789"
    assert dialog.values["Build-Zeitpunkt"].text() == "2026-10-01 08:45 UTC"
    assert "(nicht vorhanden)" in dialog.values["Richtlinie"].text()
    assert "Protokolle: " in dialog.text()


def test_qt_standard_buttons_are_german(qapp: QApplication) -> None:
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Abbrechen"
    assert QCoreApplication.translate("QPlatformTheme", "Save") == "Speichern"
