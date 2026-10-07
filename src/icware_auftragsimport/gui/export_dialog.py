"""Exportdialog: Zusammenfassung, Start, Ergebnisübersicht."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app.presentation import Glyph, Tone, plural
from ..app.workbench import ExportPlan, Workbench
from ..domain.status import ExportMode
from ..infrastructure.repositories import OrderRepository
from ..services.export_service import ExportReport, ExportResult
from .theme import TONES
from .widgets import Notice, muted, paint_glyph, section_label

RESULT_LOOK = {
    ExportResult.SUCCESS: ("Exportiert", Tone.SUCCESS),
    ExportResult.BLOCKED: ("Nicht ausgeführt", Tone.WARNING),
    ExportResult.FAILED: ("Fehlgeschlagen", Tone.DANGER),
    ExportResult.UNCLEAR: ("Unklar", Tone.DANGER),
}


class Metric(QWidget):
    """Kennzahl mit Statuszeichen, etwa „3 Aufträge bereit“."""

    def __init__(self, glyph: Glyph, tone: Tone, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.glyph, self.tone = glyph, tone
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 2, 0, 2)
        self.label = QLabel(text)
        self.label.setObjectName("Metric")
        layout.addWidget(self.label)
        layout.addStretch(1)

    def paintEvent(self, event: object) -> None:
        """Zeichen links neben dem Text."""
        painter = QPainter(self)
        paint_glyph(
            painter, QPointF(9, self.height() / 2), self.glyph, QColor(TONES[self.tone][0]), 12
        )
        painter.end()


class ExportDialog(QDialog):
    """Exportiert alle freigegebenen Aufträge in einem Schritt."""

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.plan: ExportPlan = workbench.export_plan()
        self.reports: list[ExportReport] = []
        self.setWindowTitle("Exportieren")
        self.setMinimumWidth(640)
        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.addWidget(self.stack)
        self.stack.addWidget(self._summary_page())
        self.stack.addWidget(self._result_page())

    def _summary_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title = QLabel("Export vorbereiten")
        title.setObjectName("DetailTitle")
        layout.addWidget(title)
        summary = self.plan.summary
        layout.addWidget(Metric(Glyph.CHECK, Tone.SUCCESS, summary.ready_text))
        blocked_tone = Tone.DANGER if summary.blocked else Tone.DONE
        layout.addWidget(
            Metric(
                Glyph.SQUARE if summary.blocked else Glyph.DASH, blocked_tone, summary.blocked_text
            )
        )
        if self.plan.blocked:
            orders = OrderRepository(self.workbench._conn)
            lines = [
                f"{self._label(orders, order_id)}: {reason}"
                for order_id, reason in self.plan.blocked
            ]
            notice = Notice()
            notice.show_text(Tone.DANGER, "Werden nicht exportiert:\n" + "\n".join(lines))
            layout.addWidget(notice)
        layout.addSpacing(6)
        layout.addWidget(section_label("Exportziel"))
        profile = self.workbench.profile
        self.test_mode = QRadioButton("Testexport")
        self.live_mode = QRadioButton("Produktivexport in den Lexware-Importordner")
        group = QButtonGroup(self)
        group.addButton(self.test_mode)
        group.addButton(self.live_mode)
        layout.addWidget(self.test_mode)
        layout.addWidget(muted(f"Ordner: {profile.test_export_dir or 'nicht eingerichtet'}"))
        layout.addWidget(self.live_mode)
        layout.addWidget(muted(f"Ordner: {profile.export_dir or 'nicht eingerichtet'}"))
        allowed = self.workbench.production_allowed
        self.live_mode.setEnabled(allowed)
        (self.live_mode if allowed else self.test_mode).setChecked(True)
        if not allowed:
            lock = Notice()
            lock.show_text(
                Tone.INFO,
                "Produktivexport gesperrt: Zielsystemvalidierung ausstehend. "
                "Testexporte sind möglich.",
            )
            layout.addWidget(lock)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        self.start = QPushButton("Export starten")
        self.start.setProperty("role", "primary")
        self.start.setDefault(True)
        self.start.setEnabled(summary.can_start)
        self.start.clicked.connect(self._run)
        buttons.addWidget(cancel)
        buttons.addWidget(self.start)
        layout.addLayout(buttons)
        return page

    @staticmethod
    def _label(orders: OrderRepository, order_id: str) -> str:
        order = orders.get(order_id)
        invoice = order.invoice_address.value
        company = (invoice.company or invoice.name) if invoice else order_id
        return f"{order.document_number or ''} {company}".strip()

    def _result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.result_title = QLabel("Export abgeschlossen")
        self.result_title.setObjectName("DetailTitle")
        layout.addWidget(self.result_title)
        self.result_line = muted()
        layout.addWidget(self.result_line)
        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(["Beleg", "Kunde", "Ergebnis", "Datei / Meldung"])
        self.results.verticalHeader().hide()
        self.results.setShowGrid(False)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.results.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.results, 1)
        buttons = QHBoxLayout()
        self.open_folder = QPushButton("Ordner öffnen")
        self.open_folder.clicked.connect(self._open_folder)
        buttons.addWidget(self.open_folder)
        buttons.addStretch(1)
        close = QPushButton("Schließen")
        close.setProperty("role", "primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        self.close_button = close
        layout.addLayout(buttons)
        return page

    def selected_mode(self) -> ExportMode:
        """Gewählter Exportmodus."""
        return ExportMode.LIVE if self.live_mode.isChecked() else ExportMode.TEST

    def _run(self) -> None:
        self.start.setEnabled(False)
        self.reports = self.workbench.export(self.plan.ready_ids, self.selected_mode())
        self.show_results(self.reports)

    def show_results(self, reports: list[ExportReport]) -> None:
        """Ergebnisübersicht nach dem Export."""
        orders = OrderRepository(self.workbench._conn)
        counts = {result: sum(1 for r in reports if r.result is result) for result in ExportResult}
        self.result_line.setText(
            f"{plural(counts[ExportResult.SUCCESS], 'Auftrag', 'Aufträge')} exportiert · "
            f"{counts[ExportResult.BLOCKED]} nicht ausgeführt · "
            f"{counts[ExportResult.FAILED]} fehlgeschlagen"
        )
        self.results.setRowCount(len(reports))
        for index, report in enumerate(reports):
            text, tone = RESULT_LOOK[report.result]
            detail = (
                Path(report.export_path).name
                if report.export_path
                else report.message.split("\n")[0]
            )
            cells = (
                report.document_number or "–",
                self._label(orders, report.order_id).replace(report.document_number, "").strip(),
                text,
                detail,
            )
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(QColor(TONES[tone][0]))
                if column == 3:
                    item.setToolTip(report.message)
                self.results.setItem(index, column, item)
        self.stack.setCurrentIndex(1)
        self.close_button.setDefault(True)
        self.close_button.setFocus()

    def _open_folder(self) -> None:
        mode = self.selected_mode()
        folder = (
            self.workbench.profile.export_dir
            if mode is ExportMode.LIVE
            else self.workbench.profile.test_export_dir
        )
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
