"""Fehlerdialog: Fehler-ID, Zeitpunkt, Hinweis zum Support. Nie die Fehlermeldung selbst."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..app.presentation import Tone
from ..security.crash import CrashRecord
from .widgets import Notice, muted

RATE_WINDOW_SECONDS = 60
MAX_DIALOGS_PER_WINDOW = 3


class CrashDialog(QDialog):
    """Meldet einen unerwarteten Fehler ruhig und ohne technische Details."""

    def __init__(
        self, record: CrashRecord, support_contact: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.record = record
        self.quit_requested = False
        self.setWindowTitle("Unerwarteter Fehler")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel("Bei dieser Aktion ist ein unerwarteter Fehler aufgetreten.")
        title.setObjectName("DetailTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        info = muted(
            "Gespeicherte Aufträge, Kataloge und Einstellungen sind nicht betroffen. "
            "Nicht gespeicherte Eingaben im geöffneten Auftrag können verloren sein."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.error_id = QLabel(record.error_id)
        self.error_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_id.setStyleSheet("font-weight: 600;")
        local = record.occurred.astimezone()
        self.occurred = QLabel(f"{local:%d.%m.%Y %H:%M:%S}")
        form.addRow("Fehler-ID", self.error_id)
        form.addRow("Zeitpunkt", self.occurred)
        layout.addLayout(form)
        self.support = Notice()
        self.support.show_text(
            Tone.INFO,
            f"Bitte nennen Sie dem Support die Fehler-ID ({support_contact}). Der "
            "Fehlerbericht enthält keine Passwörter, keine Mailinhalte und keine Kundendaten.",
        )
        layout.addWidget(self.support)
        buttons = QHBoxLayout()
        copy = QPushButton("Fehler-ID kopieren")
        copy.clicked.connect(self.copy_id)
        folder = QPushButton("Berichtsordner öffnen")
        folder.setEnabled(record.report is not None)
        folder.clicked.connect(self.open_folder)
        quit_button = QPushButton("Programm beenden")
        quit_button.clicked.connect(self._quit)
        resume = QPushButton("Weiterarbeiten")
        resume.setProperty("role", "primary")
        resume.setDefault(True)
        resume.clicked.connect(self.accept)
        for button in (copy, folder):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(quit_button)
        buttons.addWidget(resume)
        layout.addLayout(buttons)

    def copy_id(self) -> None:
        """Fehler-ID in die Zwischenablage."""
        QGuiApplication.clipboard().setText(self.record.error_id)

    def open_folder(self) -> None:
        """Öffnet den Ordner mit den Fehlerberichten im Explorer."""
        if self.record.report is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.record.report.parent)))

    def _quit(self) -> None:
        self.quit_requested = True
        self.reject()


class CrashReporter(QObject):
    """Brücke von beliebigen Threads in den GUI-Thread; begrenzt die Zahl der Dialoge."""

    reported = Signal(object)

    def __init__(self, support_contact: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.support_contact = support_contact
        self._shown: list[float] = []
        self._open = False
        self.reported.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def notify(self, record: CrashRecord) -> None:
        """Aus jedem Thread aufrufbar."""
        self.reported.emit(record)

    def _show(self, record: CrashRecord) -> None:
        now = time.monotonic()
        self._shown = [t for t in self._shown if now - t < RATE_WINDOW_SECONDS]
        if self._open or len(self._shown) >= MAX_DIALOGS_PER_WINDOW:
            return
        self._shown.append(now)
        self._open = True
        try:
            dialog = CrashDialog(record, self.support_contact, QApplication.activeWindow())
            dialog.exec()
            if dialog.quit_requested:
                QApplication.exit(1)
        finally:
            self._open = False
