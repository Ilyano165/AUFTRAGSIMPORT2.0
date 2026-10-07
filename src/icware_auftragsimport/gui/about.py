"""Info-Dialog (F1): Version, Build, Commit, Build-Zeitpunkt, Ablageorte, Tastenkürzel."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..branding import PRODUCT_NAME, PUBLISHER
from ..buildinfo import BuildInfo, current
from ..infrastructure.paths import AppPaths
from .widgets import muted, section_label


def install_dir() -> Path:
    """Programmordner: bei der installierten EXE deren Ordner, sonst das Paket."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def info_lines(
    info: BuildInfo, paths: AppPaths | None, shortcuts: tuple[tuple[str, str], ...] = ()
) -> list[tuple[str, str]]:
    """Alle Angaben als (Beschriftung, Wert); auch für „Angaben kopieren“."""
    lines = [
        ("Version", info.version),
        ("Build", info.build),
        ("Commit", info.short_commit),
        ("Build-Zeitpunkt", info.timestamp_text),
        (
            "Laufzeit",
            f"Python {platform.python_version()}, Qt {qVersion()}, PySide6 {pyside_version}",
        ),
        ("System", platform.platform(terse=True)),
        ("Programm", str(install_dir())),
    ]
    if paths is not None:
        policy = str(paths.policy_file) + (
            "" if paths.policy_file.exists() else " (nicht vorhanden)"
        )
        lines += [
            ("Einstellungen", str(paths.config_dir)),
            ("Daten", str(paths.root)),
            ("Protokolle", str(paths.log_dir)),
            ("Fehlerberichte", str(paths.crash_dir)),
            ("Richtlinie", policy),
        ]
    return lines + list(shortcuts)


class AboutDialog(QDialog):
    """Über das Programm."""

    def __init__(
        self,
        paths: AppPaths | None,
        shortcuts: tuple[tuple[str, str], ...],
        parent: QWidget | None = None,
        info: BuildInfo | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info or current()
        self.paths = paths
        self.setWindowTitle(f"Über {PRODUCT_NAME}")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(8)
        title = QLabel(PRODUCT_NAME)
        title.setObjectName("DetailTitle")
        layout.addWidget(title)
        layout.addWidget(muted(f"Herausgeber: {PUBLISHER}"))
        self.values: dict[str, QLabel] = {}
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        all_lines = info_lines(self.info, paths)
        for label, value in all_lines:
            if label == "Programm":
                layout.addLayout(form)
                layout.addWidget(section_label("Ablageorte"))
                form = QFormLayout()
                form.setHorizontalSpacing(16)
            widget = QLabel(value)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            widget.setWordWrap(True)
            self.values[label] = widget
            form.addRow(label, widget)
        layout.addLayout(form)
        layout.addWidget(section_label("Tastenkürzel"))
        keys = QFormLayout()
        for combo, meaning in shortcuts:
            keys.addRow(combo, QLabel(meaning))
        layout.addLayout(keys)
        self.shortcuts = shortcuts
        buttons = QHBoxLayout()
        copy = QPushButton("Angaben kopieren")
        copy.clicked.connect(self.copy)
        logs = QPushButton("Protokollordner öffnen")
        logs.setEnabled(paths is not None)
        logs.clicked.connect(
            lambda: (
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir))) if paths else None
            )
        )
        close = QPushButton("Schließen")
        close.setProperty("role", "primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(copy)
        buttons.addWidget(logs)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def text(self) -> str:
        """Angaben als Text (für den Support)."""
        return "\n".join(
            f"{label}: {value}"
            for label, value in [(PRODUCT_NAME, ""), *info_lines(self.info, self.paths)]
        )

    def copy(self) -> None:
        """In die Zwischenablage."""
        QGuiApplication.clipboard().setText(self.text())
