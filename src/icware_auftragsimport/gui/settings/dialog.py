"""Einstellungsfenster mit Navigation links."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...app.workbench import Workbench
from ...services.mail_fetch import SourceFactory
from .accounts import AccountsPage
from .catalog import CatalogPage
from .profiles import ProfilesPage

PAGES = ("Firmenprofile", "Postfächer", "Artikelkatalog")


class SettingsDialog(QDialog):
    """Einstellungen → Firmenprofile, Postfächer, Artikelkatalog."""

    def __init__(
        self,
        workbench: Workbench,
        parent: QWidget | None = None,
        *,
        mail_sources: SourceFactory | None = None,
        test_mailbox: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setMinimumSize(880, 580)
        self.resize(1120, 740)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(200)
        self.nav.addItems(list(PAGES))
        self.nav.setAccessibleName("Einstellungsbereiche")
        self.profiles = ProfilesPage(workbench)
        self.accounts = AccountsPage(workbench, mail_sources, test_mailbox=test_mailbox)
        self.catalog = CatalogPage(workbench)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.profiles)
        self.stack.addWidget(self.accounts)
        self.stack.addWidget(self.catalog)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 0, 16, 0)
        footer.addStretch(1)
        close = QPushButton("Schließen")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        outer.addLayout(footer)
        self.nav.currentRowChanged.connect(self._page_changed)
        self.profiles.profiles_changed.connect(self.catalog.reload)
        self.accounts.accounts_changed.connect(self._accounts_changed)
        self.nav.setCurrentRow(0)

    def _accounts_changed(self) -> None:
        current = self.profiles.original.id if self.profiles.original else None
        self.profiles.reload(select=current)

    def _confirm_current(self) -> bool:
        page = self.stack.currentWidget()
        if page is self.profiles:
            return self.profiles.confirm_leave()
        if page is self.accounts:
            return self.accounts.confirm_leave()
        return True

    def _page_changed(self, row: int) -> None:
        leaving = row != self.stack.currentIndex()
        if leaving and not self._confirm_current():
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(self.stack.currentIndex())
            self.nav.blockSignals(False)
            return
        self.stack.setCurrentIndex(max(row, 0))
        if self.stack.currentWidget() is self.catalog:
            self.catalog.reload()

    def open_page(self, name: str) -> None:
        """Springt zu einer Seite („Postfächer“, „Artikelkatalog“ …)."""
        if name in PAGES:
            self.nav.setCurrentRow(PAGES.index(name))

    def open_catalog(self) -> None:
        """Springt zu Einstellungen → Artikelkatalog."""
        self.nav.setCurrentRow(PAGES.index("Artikelkatalog"))

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ungespeicherte Änderungen abfragen."""
        if self._confirm_current():
            event.accept()
        else:
            event.ignore()

    def keyPressEvent(self, event: object) -> None:
        """Esc schließt über die gleiche Rückfrage wie das Fenster."""
        from PySide6.QtGui import QKeyEvent  # noqa: PLC0415

        if isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)  # type: ignore[arg-type]
