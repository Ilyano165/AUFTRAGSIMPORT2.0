"""Einstellungen → Artikelkatalog: Übersicht und Aktionen."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...app.presentation import Tone, format_money, plural
from ...app.workbench import Workbench
from ...catalog.diff import CatalogChanges
from ...catalog.export import ExportFormat
from ...catalog.model import CatalogIssue
from ...domain.errors import AppError
from ...domain.findings import Severity
from ...domain.models import Article
from ...infrastructure.atomic import atomic_write_bytes
from ..theme import TOKENS, TONES
from ..widgets import SEVERITY_TONE, Notice, muted
from .catalog_dialogs import BackupDialog, ImportDialog, RematchDialog, VersionsDialog

ARTICLE_HEADERS = (
    "Artikelnummer",
    "Bezeichnung",
    "Alias",
    "Preis",
    "Steuersatz",
    "Einheit",
    "Status",
)
SEVERITY_MARK = {Severity.ERROR: "■", Severity.WARNING: "▲", Severity.INFO: "○"}
EXPORT_FILTERS = {
    "Excel-Arbeitsmappe (*.xlsx)": ExportFormat.XLSX,
    "CSV für Excel (*.csv)": ExportFormat.CSV,
    "JSON (*.json)": ExportFormat.JSON,
}


def when(iso: str) -> str:
    """ISO-Zeitpunkt lokal und deutsch."""
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%d.%m.%Y %H:%M}"
    except ValueError:
        return iso


class ArticleModel(QAbstractTableModel):
    """Artikel der aktiven Katalogfassung."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.articles: list[Article] = []

    def set_articles(self, articles: list[Article]) -> None:
        """Ersetzt alle Artikel."""
        self.beginResetModel()
        self.articles = articles
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        """Anzahl Artikel."""
        return 0 if parent.isValid() else len(self.articles)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        """Anzahl Spalten."""
        return 0 if parent.isValid() else len(ARTICLE_HEADERS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Spaltenköpfe."""
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ARTICLE_HEADERS[section]
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Zelleninhalt."""
        if not index.isValid():
            return None
        article = self.articles[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            values = (
                article.number,
                article.name,
                ", ".join(article.aliases),
                format_money(article.price) if article.price is not None else "–",
                f"{article.tax_rate:g} %" if article.tax_rate is not None else "–",
                article.unit or "–",
                "aktiv" if article.active else "inaktiv",
            )
            return values[column]
        if role == Qt.ItemDataRole.ForegroundRole and not article.active:
            return QColor(TOKENS.text_disabled)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (3, 4):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class CatalogPage(QWidget):
    """Katalog des aktiven Profils mit allen Katalogaktionen."""

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("DetailTitle")
        self.info = muted()
        self.info.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.info)
        layout.addLayout(self._actions())
        self._build_table(layout)
        self.notice = Notice()
        layout.addWidget(self.notice)
        self.issues = QListWidget()
        self.issues.setMaximumHeight(140)
        self.issues.hide()
        layout.addWidget(self.issues)
        self.reload()

    def _actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for key, text, tip, handler in (
            ("import", "Importieren …", "CSV, TXT, XLSX oder JSON einlesen", self.import_catalog),
            (
                "export",
                "Exportieren …",
                "Aktive Version als XLSX, CSV oder JSON speichern",
                self.export_catalog,
            ),
            (
                "validate",
                "Validieren",
                "Aktiven Katalog mit den heutigen Profilregeln prüfen",
                self.validate,
            ),
            (
                "rematch",
                "Neu zuordnen …",
                "Offene Aufträge gegen den aktiven Katalog zuordnen",
                self.rematch,
            ),
            ("backup", "Backup …", "Sicherungen anlegen und wiederherstellen", self.backups),
            ("versions", "Versionen …", "Verlauf, Änderungen und Rücksprung", self.versions),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            self.buttons[key] = button
            actions.addWidget(button)
        self.buttons["import"].setProperty("role", "primary")
        actions.addStretch(1)
        return actions

    def _build_table(self, layout: QVBoxLayout) -> None:
        self.search = QLineEdit()
        self.search.setPlaceholderText("Artikel suchen (Nummer, Name, Alias)")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.model = ArticleModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(TOKENS.row_height)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in range(len(ARTICLE_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

    def reload(self) -> None:
        """Aktive Fassung des aktiven Profils anzeigen."""
        profile = self.workbench.profile
        version, articles = self.workbench.catalogs.active(profile.id)
        self.title.setText(f"Artikelkatalog · {profile.name}")
        if version is None:
            self.info.setText("Noch kein Katalog importiert. Zuordnungen sind erst danach möglich.")
        else:
            changes = CatalogChanges.from_json(version.changes).summary if version.changes else ""
            self.info.setText(
                f"Version {version.number} · {version.article_count} Artikel · "
                f"importiert am {when(version.imported_at)} aus {version.source_name}"
                + (f" · {changes}" if changes else "")
            )
        self.model.set_articles(articles)
        for key in ("export", "validate", "backup", "rematch"):
            self.buttons[key].setEnabled(version is not None)
        self.notice.hide()
        self.issues.hide()

    def import_catalog(self) -> None:
        """Importassistent."""
        dialog = ImportDialog(self.workbench, self)
        dialog.exec()
        self.reload()

    def export_catalog(self) -> None:
        """Speichert die aktive Fassung."""
        profile = self.workbench.profile
        version, _ = self.workbench.catalogs.active(profile.id)
        suggested = f"katalog-{profile.id}-v{version.number if version else 0}.xlsx"
        path, chosen = QFileDialog.getSaveFileName(
            self, "Katalog exportieren", suggested, ";;".join(EXPORT_FILTERS)
        )
        if path:
            self.export_to(Path(path), EXPORT_FILTERS.get(chosen))

    def export_to(self, path: Path, fmt: ExportFormat | None = None) -> None:
        """Export in eine Datei; das Format folgt der Endung, wenn nicht angegeben."""
        fmt = fmt or {".csv": ExportFormat.CSV, ".json": ExportFormat.JSON}.get(
            path.suffix.lower(), ExportFormat.XLSX
        )
        if path.suffix.lower() != f".{fmt.value}":
            path = path.with_suffix(f".{fmt.value}")
        data = self.workbench.catalogs.export(self.workbench.profile, fmt)
        try:
            atomic_write_bytes(path, data)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Export nicht möglich",
                f"{path.name} konnte nicht gespeichert werden ({exc.strerror}).",
            )
            return
        self.notice.show_text(Tone.SUCCESS, f"Exportiert: {path}")

    def validate(self) -> None:
        """Prüft den aktiven Katalog mit den heutigen Profilregeln."""
        issues = self.workbench.catalogs.validate_active(self.workbench.profile)
        errors = sum(1 for i in issues if i.severity is Severity.ERROR)
        warnings = sum(1 for i in issues if i.severity is Severity.WARNING)
        tone = Tone.DANGER if errors else (Tone.WARNING if warnings else Tone.SUCCESS)
        text = (
            "Katalog geprüft: keine Befunde."
            if not issues
            else (f"Katalog geprüft: {errors} Fehler, {plural(warnings, 'Hinweis', 'Hinweise')}.")
        )
        self.notice.show_text(tone, text)
        show_issues(self.issues, issues)

    def rematch(self) -> None:
        """Neuzuordnung offener Aufträge."""
        RematchDialog(self.workbench, self).exec()

    def backups(self) -> None:
        """Backups anzeigen, anlegen, wiederherstellen."""
        BackupDialog(self.workbench, self).exec()
        self.reload()

    def versions(self) -> None:
        """Versionsverlauf mit Rücksprung."""
        VersionsDialog(self.workbench, self).exec()
        self.reload()


def show_issues(target: QListWidget, issues: Sequence[CatalogIssue]) -> None:
    """Befundliste mit Zeichen in Befundfarbe."""
    target.clear()
    for issue in issues:
        severity = issue.severity
        item = QListWidgetItem(f"{SEVERITY_MARK[severity]}  {issue.render()}")
        item.setForeground(QColor(TONES[SEVERITY_TONE[severity]][0]))
        target.addItem(item)
    target.setVisible(bool(issues))


__all__ = ["AppError", "CatalogPage", "show_issues", "when"]
