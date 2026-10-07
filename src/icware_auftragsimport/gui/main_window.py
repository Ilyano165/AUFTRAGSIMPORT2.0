"""Hauptfenster: Titelleiste, Bereiche, Auftragsliste und Detailansicht."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSettings, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from ..app.presentation import CATEGORIES, Category, DetailTab, plural
from ..app.workbench import Workbench
from ..branding import PRODUCT_NAME, VERSION
from ..config.policy import MachinePolicy
from ..domain.models import Article
from ..domain.status import OrderStatus
from ..infrastructure.paths import AppPaths
from ..services.mail_fetch import FetchFailure, FetchProgress, FetchSummary, SourceFactory
from .about import AboutDialog
from .detail import DetailView, InputError
from .export_dialog import ExportDialog
from .fetch import FetchController, FetchState, FetchSummaryDialog, progress_text, state_of
from .order_table import OrderTableView
from .settings.dialog import SettingsDialog
from .theme import TOKENS

STACK_BELOW_WIDTH = 980
SHORTCUTS = (
    ("Strg+F", "Suchen"),
    ("Esc", "Suche leeren bzw. zurück zur Liste"),
    ("Enter", "Markierten Auftrag öffnen"),
    ("Pfeiltasten", "In Liste und Tabellen bewegen"),
    ("Tab / Umschalt+Tab", "Zum nächsten / vorherigen Bereich"),
    ("Strg+S", "Änderungen speichern"),
    ("Strg+,", "Einstellungen: Firmenprofile, Postfächer, Artikelkatalog"),
    ("Strg+E", "Exportieren"),
    ("Strg+1 … Strg+5", "Bereich wechseln"),
    ("Strg+Tab", "Nächster Detailbereich"),
    ("F5", "Aufträge abrufen"),
    ("F1", "Info: Version, Build, Ablageorte, Tastenkürzel"),
)


def brand_pixmap(size: int, ratio: float = 1.0) -> QPixmap:
    """IC-Ware-Zeichen: Markengrün auf Schwarz, die einzige Stelle mit #19E56A."""
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(TOKENS.brand_ground))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.18, size * 0.18)
    font = QFont(QApplication.font())
    font.setBold(True)
    font.setPixelSize(int(size * 0.5))
    painter.setFont(font)
    painter.setPen(QColor(TOKENS.brand))
    painter.drawText(QRectF(0, 0, size, size), int(Qt.AlignmentFlag.AlignCenter), "IC")
    painter.end()
    return pixmap


class BrandMark(QWidget):
    """Markenzeichen in der Titelleiste."""

    def sizeHint(self) -> QSize:
        """Quadrat in Zeilenhöhe."""
        side = self.fontMetrics().height() + 6
        return QSize(side, side)

    def paintEvent(self, event: object) -> None:
        """Zeichnet scharf in jeder Skalierung."""
        painter = QPainter(self)
        side = min(self.width(), self.height())
        painter.drawPixmap(
            QPointF(0, (self.height() - side) / 2), brand_pixmap(side, self.devicePixelRatioF())
        )
        painter.end()


class MainWindow(QMainWindow):
    """Arbeitsfläche für die tägliche Auftragserfassung."""

    def __init__(
        self,
        workbench: Workbench,
        *,
        demo: bool = False,
        persist: bool = True,
        paths: AppPaths | None = None,
        policy: MachinePolicy | None = None,
        mail_sources: SourceFactory | None = None,
    ) -> None:
        super().__init__()
        self.workbench = workbench
        self.mail_sources = mail_sources
        self.fetch_state = FetchState.NOT_CONNECTED
        self.fetcher = FetchController(self)
        self.paths = paths
        self.policy = policy or MachinePolicy()
        self.demo = demo
        self.persist = persist
        self._sized = False
        self.setWindowTitle(f"{PRODUCT_NAME}[*]")
        self.setWindowIcon(QIcon(brand_pixmap(32, 2.0)))
        self.setMinimumSize(880, 540)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._top_bar())
        layout.addWidget(self._nav_bar())
        self.table = OrderTableView()
        self.detail = DetailView(workbench)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._framed(self.table))
        self.splitter.addWidget(self._framed(self.detail))
        self.splitter.setStretchFactor(0, 11)
        self.splitter.setStretchFactor(1, 9)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self._status_bar(demo)
        self._connect()
        self._shortcuts()
        self.setTabOrder(self.search, self.categories)
        self.setTabOrder(self.categories, self.table)
        self.setTabOrder(self.table, self.detail.tabs)
        self.refresh(select_first=True)
        self._restore()

    def _status_bar(self, demo: bool) -> None:
        self.summary = QLabel()
        self.statusBar().addWidget(self.summary, 1)
        self.fetch_progress = QProgressBar()
        self.fetch_progress.setMaximumWidth(140)
        self.fetch_progress.setTextVisible(False)
        self.fetch_progress.setVisible(False)
        self.mail_status = QLabel()
        self.mail_status.setObjectName("MailStatus")
        self.mail_status.setAccessibleName("Postfachstatus")
        self.statusBar().addPermanentWidget(self.fetch_progress)
        self.statusBar().addPermanentWidget(self.mail_status)
        self._set_fetch_state(FetchState.NOT_CONNECTED)
        if demo:
            self.statusBar().addPermanentWidget(QLabel("Demo-Modus"))
        version = QPushButton(f"Version {VERSION}")
        version.setProperty("role", "link")
        version.setToolTip("Version, Build und Ablageorte (F1)")
        version.clicked.connect(self.show_shortcuts)
        self.statusBar().addPermanentWidget(version)

    @staticmethod
    def _framed(widget: QWidget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(widget)
        return holder

    def _top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(8)
        layout.addWidget(BrandMark())
        brand = QLabel("IC-Ware")
        brand.setObjectName("BrandName")
        separator = QLabel("|")
        separator.setObjectName("ProductName")
        product = QLabel("Auftrags-Import")
        product.setObjectName("ProductName")
        for widget in (brand, separator, product):
            layout.addWidget(widget)
        layout.addStretch(1)
        profile_caption = QLabel("Profil")
        profile_caption.setObjectName("TopBarInfo")
        self.profile_switch = QComboBox()
        self.profile_switch.setObjectName("ProfileSwitch")
        self.profile_switch.setToolTip("Firmenprofil wechseln")
        self.profile_switch.setAccessibleName("Firmenprofil")
        self.profile_switch.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        profile_caption.setBuddy(self.profile_switch)
        self.profile_label = QLabel()
        self.profile_label.setObjectName("TopBarInfo")
        for part in (profile_caption, self.profile_switch, self.profile_label):
            layout.addWidget(part, 0)
        self.settings_button = QPushButton("Einstellungen")
        self.settings_button.setToolTip("Firmenprofile und Artikelkatalog (Strg+,)")
        layout.addWidget(self.settings_button)
        self.fetch_button = QPushButton("Aufträge abrufen")
        self.fetch_button.setToolTip("Neue Bestellungen aus dem Postfach des Profils abrufen (F5)")
        self.cancel_fetch_button = QPushButton("Abbrechen")
        self.cancel_fetch_button.setToolTip("Abruf sicher abbrechen")
        self.cancel_fetch_button.setVisible(False)
        self.export_button = QPushButton("Exportieren")
        self.export_button.setProperty("role", "primary")
        self.export_button.setToolTip("Freigegebene Aufträge exportieren (Strg+E)")
        layout.addWidget(self.fetch_button)
        layout.addWidget(self.cancel_fetch_button)
        layout.addWidget(self.export_button)
        return bar

    def _nav_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("NavBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 12, 0)
        layout.setSpacing(8)
        self.categories = QTabBar()
        self.categories.setObjectName("CategoryTabs")
        self.categories.setDrawBase(False)
        self.categories.setExpanding(False)
        self.categories.setUsesScrollButtons(True)
        self.categories.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        for index, category in enumerate(CATEGORIES):
            self.categories.addTab(category.label)
            self.categories.setTabToolTip(index, f"{category.description} (Strg+{index + 1})")
        layout.addWidget(self.categories, 1)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Suchen (Strg+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Aufträge durchsuchen")
        layout.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _connect(self) -> None:
        self.categories.currentChanged.connect(self._category_changed)
        self.search.textChanged.connect(self._search_changed)
        self.table.order_selected.connect(self._order_selected)
        self.table.open_requested.connect(self.detail.focus_content)
        self.detail.save_requested.connect(self.save)
        self.detail.approve_requested.connect(self.approve)
        self.detail.ignore_requested.connect(self.ignore)
        self.detail.dirty_changed.connect(self.setWindowModified)
        self.fetch_button.clicked.connect(self.fetch)
        self.cancel_fetch_button.clicked.connect(self.cancel_fetch)
        self.fetcher.progress.connect(self._fetch_progress)
        self.fetcher.finished.connect(self._fetch_finished)
        self.export_button.clicked.connect(self.open_export)
        self.settings_button.clicked.connect(self.show_settings)
        self.profile_switch.currentIndexChanged.connect(self._profile_changed)
        self.detail.candidate_accepted.connect(self.accept_candidate)

    def _shortcuts(self) -> None:
        bindings = (
            (QKeySequence.StandardKey.Find, self.focus_search),
            (QKeySequence.StandardKey.Save, self.save),
            (QKeySequence("Ctrl+E"), self.open_export),
            (QKeySequence(Qt.Key.Key_F5), self.fetch),
            (QKeySequence(Qt.Key.Key_F1), self.show_shortcuts),
            (QKeySequence(Qt.Key.Key_Escape), self.escape),
            (QKeySequence("Ctrl+,"), self.show_settings),
        )
        self.actions_: list[QAction] = []
        for sequence, handler in bindings:
            action = QAction(self)
            action.setShortcut(sequence)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(handler)
            self.addAction(action)
            self.actions_.append(action)
        for index in range(len(CATEGORIES)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self.categories.setCurrentIndex(i))

    def reload(self) -> None:
        """Liste neu laden."""
        self.refresh()

    # ---------- Aufträge abrufen ----------

    def _set_fetch_state(self, state: FetchState, detail: str = "") -> None:
        self.fetch_state = state
        texts = {
            FetchState.NOT_CONNECTED: "Postfach: nicht verbunden",
            FetchState.RUNNING: "Postfach: Abruf läuft …",
            FetchState.DONE: "Postfach: Abruf abgeschlossen",
            FetchState.FAILED: "Postfach: Abruf fehlgeschlagen",
            FetchState.CANCELLED: "Postfach: Abruf abgebrochen",
        }
        self.mail_status.setText(texts[state] + (f" · {detail}" if detail else ""))
        running = state is FetchState.RUNNING
        self.fetch_button.setEnabled(not running)
        self.fetch_button.setText("Abruf läuft …" if running else "Aufträge abrufen")
        self.cancel_fetch_button.setVisible(running)
        self.cancel_fetch_button.setEnabled(running)
        self.fetch_progress.setVisible(running)

    def fetch(self) -> None:
        """F5: neue Bestellungen im Hintergrund abrufen; die Oberfläche bleibt bedienbar."""
        if self.fetcher.running:
            return
        if self.mail_sources is None:
            self.statusBar().showMessage("In dieser Sitzung ist kein Postfach verfügbar", 4000)
            return
        job = self.workbench.fetch_job(self.mail_sources)
        if isinstance(job, FetchFailure):
            self._set_fetch_state(FetchState.NOT_CONNECTED, job.what)
            self._show_fetch_result(FetchSummary(failure=job))
            return
        self.fetch_progress.setRange(0, 0)
        self._set_fetch_state(FetchState.RUNNING)
        self.fetcher.start(job)

    def cancel_fetch(self) -> None:
        """Abruf sicher abbrechen: bereits gespeicherte Aufträge bleiben vollständig."""
        if self.fetcher.running:
            self.cancel_fetch_button.setEnabled(False)
            self.mail_status.setText("Postfach: Abruf wird abgebrochen …")
            self.fetcher.cancel()

    def _fetch_progress(self, progress: object) -> None:
        if not isinstance(progress, FetchProgress) or self.fetch_state is not FetchState.RUNNING:
            return
        if progress.total:
            self.fetch_progress.setRange(0, progress.total)
            self.fetch_progress.setValue(progress.current)
        else:
            self.fetch_progress.setRange(0, 0)
        self.mail_status.setText(f"Postfach: {progress_text(progress)}")

    def _fetch_finished(self, summary: object) -> None:
        if not isinstance(summary, FetchSummary):
            return
        moment = self.workbench.now().strftime("%H:%M")
        detail = f"{moment} · {summary.orders} neu" if summary.ok else moment
        self._set_fetch_state(state_of(summary), detail)
        self.refresh()
        if summary.orders and self.table.current_order_id() is None:
            self.table.select_order(summary.new_order_ids[0])
        self._show_fetch_result(summary)

    def _show_fetch_result(self, summary: FetchSummary) -> None:
        dialog = FetchSummaryDialog(summary, self)
        dialog.exec()
        if dialog.open_settings:
            self.open_settings(page="Postfächer")

    def refresh(self, *, select_first: bool = False) -> None:
        """Lädt die Liste neu und behält die Auswahl, wenn möglich."""
        current = self.table.current_order_id()
        rows = self.workbench.rows()
        self.table.source.set_rows(rows)
        self.table.fit_columns()
        counts = self.workbench.counts(rows)
        for index, category in enumerate(CATEGORIES):
            count = counts[category.key]
            self.categories.setTabText(
                index, f"{category.label}  {count}" if count else category.label
            )
        self._apply_filter()
        found = self.table.select_order(None if select_first else current)
        if not found and not self.table.proxy.rowCount():
            self.detail.show_snapshot(None)
        plan = self.workbench.export_plan()
        ready = plan.summary.ready
        self.export_button.setText(f"Exportieren ({ready})" if ready else "Exportieren")
        open_count = counts[Category.INBOX]
        review = counts[Category.REVIEW]
        self.summary.setText(
            f"{plural(open_count, 'offener Auftrag', 'offene Aufträge')} · {review} zur Prüfung · "
            f"{plan.summary.ready_text} zum Export · {plan.summary.blocked_text}"
        )
        self.profile_label.setText(
            "Produktivexport" if self.workbench.production_allowed else "Testexport"
        )
        self._fill_profiles()

    def _fill_profiles(self) -> None:
        self.profile_switch.blockSignals(True)
        self.profile_switch.clear()
        for profile in self.workbench.profiles:
            self.profile_switch.addItem(profile.name, profile.id)
            self.profile_switch.setItemData(
                self.profile_switch.count() - 1, profile.company_name, Qt.ItemDataRole.ToolTipRole
            )
        self.profile_switch.setCurrentIndex(
            max(0, self.profile_switch.findData(self.workbench.profile.id))
        )
        self.profile_switch.blockSignals(False)

    def _profile_changed(self, index: int) -> None:
        profile_id = self.profile_switch.itemData(index)
        if not isinstance(profile_id, str) or profile_id == self.workbench.profile.id:
            return
        if not self._confirm_discard():
            self._fill_profiles()
            return
        self.workbench.switch_profile(profile_id)
        self.detail.show_snapshot(None)
        self.refresh(select_first=True)
        self.statusBar().showMessage(f"Profil „{self.workbench.profile.name}“ aktiv", 3000)

    def show_settings(self) -> None:
        """Strg+, und Knopf „Einstellungen“."""
        self.open_settings()

    def open_settings(self, page: str = "") -> None:
        """Einstellungen: Firmenprofile, Postfächer und Artikelkatalog (Strg+,)."""
        if not self._confirm_discard():
            return
        dialog = SettingsDialog(
            self.workbench, self, mail_sources=self.mail_sources, test_mailbox=self.demo
        )
        if page:
            dialog.open_page(page)
        dialog.exec()
        self.refresh()
        if self.detail.snapshot is not None:
            self.detail.show_snapshot(self.workbench.snapshot(self.detail.snapshot.order.id))

    def accept_candidate(self, line_index: int, article: Article) -> None:
        """Übernimmt einen Vorschlag als manuelle Zuordnung und speichert."""
        if self.detail.snapshot is None:
            return
        try:
            order = self.workbench.assign_article(self.detail.collect(), line_index, article)
            snapshot = self.workbench.save(order)
        except (InputError, ValueError) as exc:
            QMessageBox.warning(self, "Zuordnung nicht möglich", str(exc))
            return
        self.detail.show_snapshot(snapshot)
        self.detail.show_tab(DetailTab.LINES)
        self.refresh()
        self.statusBar().showMessage(
            f"Position {line_index + 1}: {article.number} zugeordnet", 4000
        )

    def _apply_filter(self) -> None:
        category = CATEGORIES[max(0, self.categories.currentIndex())]
        self.table.proxy.set_statuses(category.statuses)
        self.table.proxy.set_query(self.search.text())

    def _category_changed(self, _index: int) -> None:
        if not self._confirm_discard():
            return
        self._apply_filter()
        self.table.select_order(self.table.current_order_id())
        if not self.table.proxy.rowCount():
            self.detail.show_snapshot(None)

    def _search_changed(self, _text: str) -> None:
        self._apply_filter()
        if self.table.proxy.rowCount():
            self.table.select_order(self.table.current_order_id())
        else:
            self.detail.show_snapshot(None)

    def _order_selected(self, order_id: str) -> None:
        if self.detail.snapshot and self.detail.snapshot.order.id == order_id:
            return
        if not self._confirm_discard():
            self.table.blockSignals(True)
            self.table.select_order(self.detail.snapshot.order.id if self.detail.snapshot else None)
            self.table.blockSignals(False)
            return
        self.detail.show_snapshot(self.workbench.snapshot(order_id))

    def _confirm_discard(self) -> bool:
        if not self.detail.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Die Änderungen an diesem Auftrag wurden noch nicht gespeichert.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def save(self) -> bool:
        """Speichert die Eingaben des aktuellen Auftrags (Strg+S)."""
        if not self.detail.dirty or self.detail.snapshot is None:
            return True
        try:
            snapshot = self.workbench.save(self.detail.collect())
        except (InputError, ValueError) as exc:
            QMessageBox.warning(self, "Speichern nicht möglich", str(exc))
            return False
        self.detail.show_snapshot(snapshot)
        self.refresh()
        self.statusBar().showMessage("Gespeichert", 3000)
        return True

    def approve(self) -> None:
        """Gibt den Auftrag frei und vergibt die Belegnummer."""
        snapshot = self.detail.snapshot
        if snapshot is None or snapshot.order.status is not OrderStatus.READY or not self.save():
            return
        try:
            approved = self.workbench.approve(snapshot.order.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Freigabe nicht möglich", str(exc))
            return
        self.refresh()
        self.detail.show_snapshot(approved)
        self.statusBar().showMessage(f"Freigegeben als {approved.order.document_number}", 4000)

    def ignore(self) -> None:
        """Markiert die Mail als keine Bestellung."""
        snapshot = self.detail.snapshot
        if snapshot is None:
            return
        answer = QMessageBox.question(
            self,
            "Keine Bestellung",
            "Diese Mail als keine Bestellung markieren? Sie wird unter „Erledigt“ abgelegt.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.workbench.ignore(snapshot.order.id)
            self.detail.show_snapshot(None)
            self.refresh(select_first=True)

    def open_export(self) -> None:
        """Exportdialog (Strg+E)."""
        if not self._confirm_discard():
            return
        dialog = ExportDialog(self.workbench, self)
        dialog.exec()
        self.refresh()
        if self.detail.snapshot is not None:
            self.detail.show_snapshot(self.workbench.snapshot(self.detail.snapshot.order.id))

    def focus_search(self) -> None:
        """Strg+F."""
        self.search.setFocus()
        self.search.selectAll()

    def escape(self) -> None:
        """Esc: Suche leeren, sonst zurück zur Liste."""
        focus = QApplication.focusWidget()
        if focus is self.search and self.search.text():
            self.search.clear()
        elif focus is not None and self.detail.isAncestorOf(focus):
            self.table.setFocus()
        elif self.search.text():
            self.search.clear()
            self.table.setFocus()
        else:
            self.table.setFocus()

    def show_shortcuts(self) -> None:
        """F1: Info mit Version, Build, Ablageorten und Tastenkürzeln."""
        AboutDialog(self.paths, SHORTCUTS, self).exec()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Schmale Fenster: Liste über der Detailansicht."""
        super().resizeEvent(event)
        vertical = event.size().width() < STACK_BELOW_WIDTH
        wanted = Qt.Orientation.Vertical if vertical else Qt.Orientation.Horizontal
        if self.splitter.orientation() != wanted:
            self.splitter.setOrientation(wanted)
            total = event.size().height() if vertical else event.size().width()
            first = int(total * 0.42) if vertical else total // 2
            self.splitter.setSizes([first, total - first])

    def showEvent(self, event: QShowEvent) -> None:
        """Beim ersten Anzeigen: Liste und Details je zur Hälfte, sofern nichts gespeichert ist."""
        super().showEvent(event)
        if not self._sized:
            self._sized = True
            if self.splitter.orientation() is Qt.Orientation.Horizontal:
                total = self.splitter.width()
                self.splitter.setSizes([total // 2, total - total // 2])

    def _settings(self) -> QSettings:
        return QSettings("IC-Ware", "Auftrags-Import")

    def _restore(self) -> None:
        if not self.persist:
            return
        settings = self._settings()
        geometry = settings.value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        state = settings.value("window/splitter")
        if isinstance(state, QByteArray):
            self._sized = self.splitter.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Fragt bei ungespeicherten Änderungen und merkt Fenstergröße und Aufteilung."""
        if not self._confirm_discard():
            event.ignore()
            return
        if self.fetcher.running:
            self.fetcher.cancel()
            self.fetcher.wait(10_000)
        if self.persist:
            settings = self._settings()
            settings.setValue("window/geometry", self.saveGeometry())
            settings.setValue("window/splitter", self.splitter.saveState())
        event.accept()
