"""Dialoge zum Artikelkatalog: Import-Assistent, Versionen, Backups, Neuzuordnung."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...app.presentation import Tone, format_money, plural
from ...app.workbench import Workbench
from ...catalog.diff import CatalogChanges
from ...catalog.mapping import ColumnMapping, PriceBasis, map_rows
from ...catalog.model import CatalogField
from ...catalog.service import ImportOutcome, ImportPreview
from ...catalog.table import (
    DELIMITER_LABELS,
    CatalogSourceError,
    RawTable,
    SourceKind,
    TableOptions,
)
from ...config.schema import PriceMode
from ...domain.errors import AppError
from ...domain.findings import Severity
from ...security.limits import FILES
from ..theme import TONES
from ..widgets import Notice, muted, section_label

STEPS = ("Datei", "Zuordnung", "Prüfung", "Ergebnis")
PREVIEW_ROWS = 8
NOT_MAPPED = -1
SOURCE_LABELS = {"csv": "CSV/Text", "xlsx": "Excel", "json": "JSON", "backup": "Wiederherstellung"}


def _table(headers: Sequence[str], *, stretch: int | None = None) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().hide()
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for column in range(len(headers)):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    if stretch is not None:
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return table


def _fill(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
    table.setRowCount(len(rows))
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            table.setItem(r, c, item)


def _when(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%d.%m.%Y %H:%M}"
    except ValueError:
        return iso


class ImportDialog(QDialog):
    """Assistent: Datei → Zuordnung → Prüfung → Ergebnis; gespeichert wird erst im 3. Schritt."""

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.profile = workbench.profile
        self.data: bytes = b""
        self.source_name = ""
        self.table: RawTable | None = None
        self.preview: ImportPreview | None = None
        self.outcome: ImportOutcome | None = None
        self._loading = False
        self.setWindowTitle(f"Artikelkatalog importieren · {self.profile.name}")
        self.setMinimumSize(820, 560)
        self.resize(980, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        steps = QHBoxLayout()
        self.step_labels = [QLabel(f"{i + 1}  {name}") for i, name in enumerate(STEPS)]
        for label in self.step_labels:
            steps.addWidget(label)
        steps.addStretch(1)
        layout.addLayout(steps)
        self.stack = QStackedWidget()
        for page in (
            self._file_page(),
            self._mapping_page(),
            self._check_page(),
            self._result_page(),
        ):
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        buttons = QHBoxLayout()
        self.back_button = QPushButton("Zurück")
        self.next_button = QPushButton("Weiter")
        self.import_button = QPushButton("Importieren")
        self.close_button = QPushButton("Abbrechen")
        for button in (self.next_button, self.import_button):
            button.setProperty("role", "primary")
        self.back_button.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next_button.clicked.connect(lambda: self.go(self.stack.currentIndex() + 1))
        self.import_button.clicked.connect(self.run_import)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.back_button)
        buttons.addStretch(1)
        for button in (self.close_button, self.next_button, self.import_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.go(0)

    # ---------- Schritt 1: Datei ----------
    def _file_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            muted(
                "Unterstützt: Excel (.xlsx), CSV und Text (.csv, .txt, etwa der ASCII-Export aus "
                "Lexware), JSON. Gespeichert wird erst nach der Prüfung."
            )
        )
        layout.itemAt(0).widget().setWordWrap(True)  # type: ignore[union-attr]
        choose = QPushButton("Datei auswählen …")
        choose.clicked.connect(self.choose_file)
        self.file_label = muted("Keine Datei gewählt")
        top = QHBoxLayout()
        top.addWidget(choose)
        top.addWidget(self.file_label, 1)
        layout.addLayout(top)
        form = QFormLayout()
        self.has_header = QCheckBox("Erste Zeile enthält Spaltennamen")
        self.has_header.setChecked(True)
        self.delimiter = QComboBox()
        self.delimiter.addItem("Automatisch erkennen", "")
        for symbol, label in DELIMITER_LABELS.items():
            self.delimiter.addItem(label, symbol)
        self.sheet = QComboBox()
        for widget in (self.has_header, self.delimiter, self.sheet):
            signal = widget.toggled if isinstance(widget, QCheckBox) else widget.currentIndexChanged
            signal.connect(self._options_changed)
        form.addRow("", self.has_header)
        form.addRow("Trennzeichen", self.delimiter)
        form.addRow("Tabellenblatt", self.sheet)
        layout.addLayout(form)
        self.file_notice = Notice()
        layout.addWidget(self.file_notice)
        layout.addWidget(section_label("Inhalt der Datei"))
        self.raw = _table(())
        layout.addWidget(self.raw, 1)
        return page

    def choose_file(self) -> None:
        """Dateiauswahl im Windows-Dialog."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Artikelkatalog auswählen",
            "",
            "Kataloge (*.xlsx *.csv *.txt *.json);;Alle Dateien (*)",
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        """Liest eine Datei mit Größenprüfung vor dem Einlesen."""
        try:
            if path.stat().st_size > FILES.catalog_max_bytes:
                raise CatalogSourceError(
                    f"Datei größer als {FILES.catalog_max_bytes // (1024 * 1024)} MB"
                )
            data = path.read_bytes()
        except (OSError, CatalogSourceError) as exc:
            self.file_notice.show_text(Tone.DANGER, f"Datei nicht lesbar: {exc}")
            return
        self.load_bytes(data, path.name)

    def load_bytes(self, data: bytes, name: str) -> None:
        """Übernimmt Dateiinhalt (auch für Tests) und liest ihn mit den aktuellen Optionen."""
        self.data, self.source_name = data, name
        self._loading = True
        self.sheet.clear()
        self.has_header.setChecked(True)
        self._loading = False
        self._read()

    def _options_changed(self, *_args: object) -> None:
        if not self._loading and self.data:
            self._read()

    def _read(self) -> None:
        options = TableOptions(
            has_header=self.has_header.isChecked(),
            delimiter=self.delimiter.currentData() or None,
            sheet=self.sheet.currentText() or None,
        )
        try:
            table = self.workbench.catalogs.read(self.data, self.source_name, options)
        except CatalogSourceError as exc:
            self.table = None
            self.file_notice.show_text(Tone.DANGER, f"{self.source_name}: {exc}")
            self.raw.setRowCount(0)
            self._update_buttons()
            return
        self.table = table
        self.file_label.setText(self.source_name)
        self.file_notice.show_text(Tone.INFO, table.description)
        self._loading = True
        if table.kind is SourceKind.XLSX and self.sheet.count() != len(table.sheets):
            self.sheet.clear()
            self.sheet.addItems(list(table.sheets))
            self.sheet.setCurrentText(table.sheet)
        self.sheet.setEnabled(table.kind is SourceKind.XLSX)
        self.delimiter.setEnabled(table.kind is SourceKind.CSV)
        self.has_header.setEnabled(table.kind is not SourceKind.JSON)
        self._loading = False
        self.raw.setColumnCount(len(table.headers))
        self.raw.setHorizontalHeaderLabels(list(table.headers))
        _fill(self.raw, [list(cells) for _, cells in table.rows[:20]])
        self._set_mapping(self.workbench.catalogs.suggest(self.profile.id, table))
        self._update_buttons()

    # ---------- Schritt 2: Zuordnung ----------
    def _mapping_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        basis = "Nettopreisen" if self.profile.price_mode is PriceMode.NET else "Bruttopreisen"
        info = muted(
            f"Pflicht: Artikelnummer und Artikelname. Das Profil rechnet mit {basis}; Preise der "
            "anderen Art werden über den Steuersatz umgerechnet (4 Nachkommastellen)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self.combos: dict[str, QComboBox] = {}
        for key, label in ((f.value, f.label) for f in CatalogField):
            combo = QComboBox()
            combo.currentIndexChanged.connect(self._mapping_changed)
            self.combos[key] = combo
            form.addRow(label + (" *" if key in ("number", "name") else ""), combo)
            if key == "aliases":
                extra = QComboBox()
                extra.currentIndexChanged.connect(self._mapping_changed)
                self.combos["aliases2"] = extra
                form.addRow("weiterer Alias", extra)
        self.price_basis = QComboBox()
        for basis_option in PriceBasis:
            self.price_basis.addItem(f"{basis_option.label}preise", basis_option.value)
        self.price_basis.currentIndexChanged.connect(self._mapping_changed)
        self.inverted = QCheckBox("Spalte gibt „inaktiv/gesperrt“ an (Ja = inaktiv)")
        self.inverted.toggled.connect(self._mapping_changed)
        form.addRow("Preise der Quelle", self.price_basis)
        form.addRow("", self.inverted)
        layout.addLayout(form)
        layout.addWidget(section_label(f"Vorschau der ersten {PREVIEW_ROWS} Artikel"))
        self.mapped = _table(
            (
                "Zeile",
                "Artikelnummer",
                "Artikelname",
                "Alias",
                "Preis",
                "Steuersatz",
                "Einheit",
                "Status",
            ),
            stretch=2,
        )
        layout.addWidget(self.mapped, 1)
        return page

    def _set_mapping(self, mapping: ColumnMapping) -> None:
        assert self.table is not None
        self._loading = True
        for key, combo in self.combos.items():
            combo.clear()
            combo.addItem("— nicht zuordnen —", NOT_MAPPED)
            for index, header in enumerate(self.table.headers):
                combo.addItem(header, index)
            target = CatalogField("aliases" if key == "aliases2" else key)
            columns = mapping.columns.get(target, ())
            position = 1 if key == "aliases2" else 0
            chosen = columns[position] if len(columns) > position else NOT_MAPPED
            combo.setCurrentIndex(max(0, combo.findData(chosen)))
        self.price_basis.setCurrentIndex(self.price_basis.findData(mapping.price_basis.value))
        self.inverted.setChecked(mapping.active_inverted)
        self._loading = False
        self._mapping_changed()

    def mapping(self) -> ColumnMapping:
        """Zuordnung aus den Auswahlfeldern."""
        columns: dict[CatalogField, tuple[int, ...]] = {}
        for target in CatalogField:
            keys = ("aliases", "aliases2") if target is CatalogField.ALIASES else (target.value,)
            chosen = tuple(
                dict.fromkeys(
                    i for k in keys if (i := self.combos[k].currentData()) not in (None, NOT_MAPPED)
                )
            )
            if chosen:
                columns[target] = chosen
        return ColumnMapping(
            columns, PriceBasis(self.price_basis.currentData() or "net"), self.inverted.isChecked()
        )

    def _mapping_changed(self, *_args: object) -> None:
        if self._loading or self.table is None:
            return
        sample = RawTable(
            self.table.source_name,
            self.table.kind,
            self.table.sha256,
            self.table.headers,
            self.table.rows[:PREVIEW_ROWS],
        )
        target = PriceBasis.GROSS if self.profile.price_mode is PriceMode.GROSS else PriceBasis.NET
        rows = map_rows(
            sample, self.mapping(), target_basis=target, tax_rates=self.profile.tax_rates
        )
        values = [
            [
                str(r.row),
                r.article.number,
                r.article.name,
                ", ".join(r.article.aliases),
                format_money(r.article.price) if r.article.price is not None else "–",
                f"{r.article.tax_rate:g} %" if r.article.tax_rate is not None else "–",
                r.article.unit,
                "aktiv" if r.article.active else "inaktiv",
            ]
            for r in rows
        ]
        _fill(self.mapped, values)
        for index, mapped in enumerate(rows):
            if any(i.severity is Severity.ERROR for i in mapped.issues):
                for column in range(self.mapped.columnCount()):
                    cell = self.mapped.item(index, column)
                    if cell is not None:
                        cell.setBackground(QColor(TONES[Tone.DANGER][1]))
                        cell.setToolTip("\n".join(i.render() for i in mapped.issues))
        self._update_buttons()

    # ---------- Schritt 3: Prüfung ----------
    def _check_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.metrics = QLabel()
        self.metrics.setObjectName("Metric")
        self.changes_label = muted()
        self.check_notice = Notice()
        layout.addWidget(self.metrics)
        layout.addWidget(self.changes_label)
        layout.addWidget(self.check_notice)
        tabs = QTabWidget()
        tabs.setObjectName("Detail")
        self.issue_list = QListWidget()
        self.change_table = _table(
            ("Artikelnummer", "Änderung", "Felder", "vorher", "nachher"), stretch=2
        )
        tabs.addTab(self.issue_list, "Befunde")
        tabs.addTab(self.change_table, "Änderungen")
        self.check_tabs = tabs
        layout.addWidget(tabs, 1)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Bemerkung zur Version (optional), z. B. „Preisliste 2027“")
        layout.addWidget(self.note)
        return page

    def check(self) -> ImportPreview | None:
        """Prüft mit der aktuellen Zuordnung und zeigt Befunde und Änderungen."""
        if self.table is None:
            return None
        from .catalog import show_issues  # noqa: PLC0415

        preview = self.workbench.catalogs.preview(self.profile, self.table, self.mapping())
        self.preview = preview
        self.metrics.setText(
            f"{plural(len(preview.rows), 'Artikel', 'Artikel')} · "
            f"{plural(len(preview.errors), 'Fehler', 'Fehler')} · "
            f"{plural(len(preview.warnings), 'Warnung', 'Warnungen')}"
        )
        self.changes_label.setText(f"Gegenüber der aktiven Version: {preview.changes.summary}")
        if preview.can_import:
            backup = (
                " Vorher wird automatisch ein Backup der aktiven Version erstellt."
                if preview.based_on
                else ""
            )
            self.check_notice.show_text(Tone.SUCCESS, "Bereit zum Import." + backup)
        else:
            self.check_notice.show_text(
                Tone.DANGER,
                f"Import nicht möglich: {plural(len(preview.errors), 'Fehler', 'Fehler')}. "
                "Der aktive Katalog bleibt unverändert. Datei korrigieren oder Zuordnung anpassen.",
            )
        show_issues(self.issue_list, list(preview.issues))
        self.issue_list.setVisible(True)
        details = preview.changes.details
        _fill(
            self.change_table,
            [[d.number, d.kind, ", ".join(d.fields), d.before, d.after] for d in details[:500]],
        )
        self.check_tabs.setTabText(0, f"Befunde ({len(preview.issues)})")
        self.check_tabs.setTabText(1, f"Änderungen ({len(details)})")
        return preview

    # ---------- Schritt 4: Ergebnis ----------
    def _result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.result_title = QLabel()
        self.result_title.setObjectName("DetailTitle")
        self.result_text = muted()
        self.result_text.setWordWrap(True)
        self.result_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result_title)
        layout.addWidget(self.result_text)
        rematch = QPushButton("Offene Aufträge neu zuordnen …")
        rematch.clicked.connect(lambda: RematchDialog(self.workbench, self).exec())
        layout.addWidget(rematch, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def run_import(self) -> None:
        """Speichert die geprüfte Fassung (atomar, nach Backup)."""
        preview = self.preview or self.check()
        if preview is None:
            return
        try:
            self.outcome = self.workbench.catalogs.commit(
                self.profile, preview, self.note.text().strip()
            )
        except AppError as exc:
            self.check_notice.show_text(Tone.DANGER, str(exc))
            return
        outcome = self.outcome
        self.result_title.setText(f"Version {outcome.number} ist aktiv")
        backup = (
            f"Backup der vorherigen Version: {outcome.backup}"
            if outcome.backup
            else "Erster Katalog dieses Profils."
        )
        self.result_text.setText(f"{outcome.changes.summary}\n{backup}")
        self.go(3)

    # ---------- Navigation ----------
    def go(self, index: int) -> None:
        """Wechselt den Schritt; Schritt 3 prüft neu."""
        index = max(0, min(index, 3))
        if index == 2:
            self.check()
        self.stack.setCurrentIndex(index)
        for position, label in enumerate(self.step_labels):
            label.setStyleSheet(
                "font-weight: 600;" if position == index else f"color: {TONES[Tone.DONE][0]};"
            )
        self._update_buttons()

    def _update_buttons(self) -> None:
        index = self.stack.currentIndex()
        done = index == 3
        self.back_button.setVisible(0 < index < 3)
        self.next_button.setVisible(index < 2)
        ready = self.table is not None and (index == 0 or not self.mapping().missing())
        self.next_button.setEnabled(ready)
        self.import_button.setVisible(index == 2)
        self.import_button.setEnabled(bool(self.preview and self.preview.can_import))
        self.close_button.setText("Schließen" if done else "Abbrechen")
        (self.import_button if index == 2 else self.next_button).setDefault(True)


class VersionsDialog(QDialog):
    """Versionsverlauf mit Änderungsstatistik und Rücksprung."""

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.setWindowTitle(f"Katalogversionen · {workbench.profile.name}")
        self.resize(980, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        self.table = _table(
            (
                "Version",
                "Importiert",
                "Quelle",
                "Art",
                "Artikel",
                "Änderungen",
                "Bemerkung",
                "Status",
            ),
            stretch=5,
        )
        self.table.currentCellChanged.connect(lambda row, *_: self._details(row))
        layout.addWidget(self.table, 2)
        layout.addWidget(section_label("Änderungen dieser Version"))
        self.details = _table(
            ("Artikelnummer", "Änderung", "Felder", "vorher", "nachher"), stretch=2
        )
        layout.addWidget(self.details, 1)
        self.notice = Notice()
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        self.activate_button = QPushButton("Diese Version aktivieren")
        self.activate_button.clicked.connect(self.activate)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.activate_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        """Liest die Versionen neu."""
        self.versions = self.workbench.catalogs.versions(self.workbench.profile.id)
        rows = [
            [
                str(v.number),
                _when(v.imported_at),
                v.source_name,
                SOURCE_LABELS.get(v.source_kind, v.source_kind or "–"),
                str(v.article_count),
                CatalogChanges.from_json(v.changes).summary if v.changes else "–",
                v.note,
                "aktiv" if v.active else "",
            ]
            for v in self.versions
        ]
        _fill(self.table, rows)
        if self.versions:
            self.table.setCurrentCell(0, 0)
        self._details(self.table.currentRow())

    def _details(self, row_number: int) -> None:
        version = self.versions[row_number] if 0 <= row_number < len(self.versions) else None
        changes = CatalogChanges.from_json(version.changes) if version else CatalogChanges()
        _fill(
            self.details,
            [[d.number, d.kind, ", ".join(d.fields), d.before, d.after] for d in changes.details],
        )
        self.activate_button.setEnabled(bool(version and not version.active))

    def activate(self) -> None:
        """Rücksprung auf die markierte Version (vorher Backup der aktiven)."""
        row_number = self.table.currentRow()
        if not 0 <= row_number < len(self.versions):
            return
        number = self.versions[row_number].version
        label = self.versions[row_number].number
        answer = QMessageBox.question(
            self,
            "Version aktivieren",
            f"Version {label} wieder aktivieren? Die aktive Version wird vorher gesichert.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.activate_version(number)

    def activate_version(self, number: int) -> None:
        """Aktiviert eine Version ohne Rückfrage (auch für Tests)."""
        try:
            backup = self.workbench.catalogs.activate(self.workbench.profile, number)
        except (AppError, KeyError) as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return
        self.reload()
        label = next((v.number for v in self.versions if v.version == number), number)
        self.notice.show_text(
            Tone.SUCCESS,
            f"Version {label} ist aktiv." + (f" Backup: {backup.name}" if backup else ""),
        )


class BackupDialog(QDialog):
    """Backups anlegen, prüfen und als neue Version wiederherstellen."""

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.setWindowTitle(f"Katalog-Backups · {workbench.profile.name}")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        info = muted(
            "Vor jedem Import und Rücksprung entsteht automatisch ein Backup. Die letzten 30 je "
            "Profil bleiben erhalten; eine Prüfsumme schützt jede Datei gegen Veränderung."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = _table(
            ("Zeitpunkt", "Version", "Artikel", "Anlass", "Zustand", "Datei"), stretch=5
        )
        layout.addWidget(self.table, 1)
        self.notice = Notice()
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        create = QPushButton("Backup jetzt erstellen")
        create.clicked.connect(self.create_backup)
        self.restore_button = QPushButton("Wiederherstellen …")
        self.restore_button.clicked.connect(self.restore)
        folder = QPushButton("Ordner öffnen")
        folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder())))
        )
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        for button in (create, self.restore_button, folder):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.reload()

    def _folder(self) -> Path:
        return self.workbench.catalogs.backups.folder(self.workbench.profile.id)

    def reload(self) -> None:
        """Liest die Backups neu."""
        self.entries = self.workbench.catalogs.backups.entries(self.workbench.profile.id)
        _fill(
            self.table,
            [
                [
                    _when(e.created) if e.created else "–",
                    str(e.catalog_version or "–"),
                    str(e.article_count),
                    e.reason,
                    "intakt" if e.intact else "beschädigt",
                    e.path.name,
                ]
                for e in self.entries
            ],
        )
        for index, entry in enumerate(self.entries):
            if not entry.intact:
                item = self.table.item(index, 4)
                if item is not None:
                    item.setForeground(QColor(TONES[Tone.DANGER][0]))
        self.restore_button.setEnabled(bool(self.entries))

    def create_backup(self) -> None:
        """Manuelles Backup der aktiven Version."""
        try:
            path = self.workbench.catalogs.backup_now(self.workbench.profile)
        except (AppError, ValueError, OSError) as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return
        self.reload()
        self.notice.show_text(Tone.SUCCESS, f"Backup erstellt: {path.name}")

    def restore(self) -> None:
        """Stellt das markierte Backup als neue Version wieder her (nach Prüfung und Rückfrage)."""
        row_number = self.table.currentRow()
        if not 0 <= row_number < len(self.entries):
            self.notice.show_text(Tone.INFO, "Bitte ein Backup markieren.")
            return
        entry = self.entries[row_number]
        try:
            preview = self.workbench.catalogs.restore(self.workbench.profile, entry.path)
        except (AppError, ValueError) as exc:
            self.notice.show_text(Tone.DANGER, f"Nicht wiederherstellbar: {exc}")
            return
        if not preview.can_import:
            self.notice.show_text(
                Tone.DANGER,
                "Nicht wiederherstellbar: " + "; ".join(i.render() for i in preview.errors[:3]),
            )
            return
        answer = QMessageBox.question(
            self,
            "Backup wiederherstellen",
            f"{entry.path.name}\n\nGegenüber der aktiven Version: {preview.changes.summary}\n\n"
            "Das Backup wird als neue Version gespeichert; die aktive wird vorher gesichert.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.apply_restore(preview, entry.path.name)

    def apply_restore(self, preview: ImportPreview, name: str) -> None:
        """Speichert die Wiederherstellung (auch für Tests)."""
        try:
            outcome = self.workbench.catalogs.commit(
                self.workbench.profile, preview, f"Wiederherstellung aus {name}"
            )
        except AppError as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return
        self.reload()
        self.notice.show_text(Tone.SUCCESS, f"Wiederhergestellt als Version {outcome.number}.")


class RematchDialog(QDialog):
    """Vorschau der Neuzuordnung offener Aufträge; übernommen wird erst nach Bestätigung."""

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.setWindowTitle(f"Neu zuordnen · {workbench.profile.name}")
        self.resize(940, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        info = muted(
            "Offene Aufträge (Neu, Prüfen, Bereit) werden gegen den aktiven Katalog zugeordnet. "
            "Manuelle Zuordnungen bleiben; freigegebene und exportierte Aufträge bleiben unberührt."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.summary = QLabel()
        self.summary.setObjectName("SectionLabel")
        layout.addWidget(self.summary)
        self.table = _table(
            ("Kunde", "Pos.", "Text in der Mail", "bisher", "neu", "Änderung"), stretch=2
        )
        layout.addWidget(self.table, 1)
        self.notice = Notice()
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        self.apply_button = QPushButton("Übernehmen")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(close)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self.plan = workbench.rematch_plan()
        self._show()

    @staticmethod
    def _describe(match: object) -> str:
        article = getattr(match, "article", None)
        status = getattr(match, "status", None)
        if article is not None:
            return f"{article.number} {article.name}"
        return (
            "prüfen"
            if status is not None and status.value == "needs_review"
            else "nicht zugeordnet"
        )

    def _show(self) -> None:
        plan = self.plan
        self.summary.setText(plan.summary)
        _fill(
            self.table,
            [
                [
                    c.company,
                    str(c.position),
                    c.description,
                    self._describe(c.before),
                    self._describe(c.after),
                    c.kind,
                ]
                for c in plan.changes
            ],
        )
        self.apply_button.setEnabled(bool(plan.changes))

    def apply(self) -> None:
        """Übernimmt die Vorschau."""
        try:
            count = self.workbench.apply_rematch(self.plan)
        except (ValueError, AppError) as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return
        self.notice.show_text(Tone.SUCCESS, f"{plural(count, 'Auftrag', 'Aufträge')} aktualisiert.")
        self.plan = self.workbench.rematch_plan()
        self._show()
