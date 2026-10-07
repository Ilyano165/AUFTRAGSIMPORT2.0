"""Hauptfenster: Aufbau, Bereiche, Suche, Tastatur, Bearbeiten, Freigabe, Export, Größen."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QTabBar, QWidget

from icware_auftragsimport.app.presentation import DetailTab, IssueView
from icware_auftragsimport.domain.status import OrderStatus
from icware_auftragsimport.gui.detail import ISSUE_ROLE
from icware_auftragsimport.gui.export_dialog import ExportDialog
from icware_auftragsimport.gui.main_window import MainWindow
from icware_auftragsimport.gui.order_table import MIN_COMPANY_WIDTH, Column

FORBIDDEN = re.compile(
    r"\bKI\b|künstlich|Intelligenz|\bAI\b(?!-\d)|Chatbot|Assistent", re.IGNORECASE
)


def _texts(root: QWidget) -> list[str]:
    texts = [w.text() for w in root.findChildren(QLabel)] + [
        b.text() for b in root.findChildren(QAbstractButton)
    ]
    for bar in root.findChildren(QTabBar):
        texts += [bar.tabText(i) for i in range(bar.count())]
    return texts


def test_brand_layout_and_no_ai_wording(window: MainWindow) -> None:
    texts = _texts(window)
    assert "IC-Ware" in texts
    assert "Auftrags-Import" in texts
    labels = [window.categories.tabText(i).split("  ")[0] for i in range(window.categories.count())]
    assert labels == ["Posteingang", "Bereit", "Prüfung erforderlich", "Erledigt", "Fehler"]
    assert window.detail.tabs.count() == 8
    assert not [t for t in texts if FORBIDDEN.search(t)]


def test_list_columns_and_status_glyph_role(window: MainWindow) -> None:
    headers = [window.table.source.headerData(c, Qt.Orientation.Horizontal) for c in Column]
    assert headers == [
        "Status",
        "Eingang",
        "Bestellnummer",
        "Firma",
        "Ansprechpartner",
        "Positionen",
        "Probleme",
    ]
    index = window.table.proxy.index(0, Column.STATUS)
    assert OrderStatus(index.data(Qt.ItemDataRole.UserRole + 1))


def test_categories_filter_the_list(window: MainWindow) -> None:
    expected = {0: 8, 1: 4, 2: 3, 3: 3, 4: 1}
    for tab, rows in expected.items():
        window.categories.setCurrentIndex(tab)
        assert window.table.proxy.rowCount() == rows


def test_shortcuts_are_registered(window: MainWindow) -> None:
    sequences = {a.shortcut().toString() for a in window.actions_}
    assert {"Ctrl+F", "Ctrl+S", "Ctrl+E", "F5", "F1", "Esc"} <= sequences
    assert QKeySequence("Ctrl+F").toString() in sequences


def test_search_and_escape(window: MainWindow, qapp: QApplication) -> None:
    window.focus_search()
    QTest.keyClicks(window.search, "Schulte")
    assert window.table.proxy.rowCount() == 1
    window.escape()
    assert window.search.text() == ""
    assert window.table.proxy.rowCount() == 8


def test_enter_opens_detail_and_escape_returns_to_list(
    window: MainWindow, qapp: QApplication
) -> None:
    window.activateWindow()
    window.table.setFocus()
    window.table.select_order("order-003")
    QTest.keyClick(window.table, Qt.Key.Key_Return)
    qapp.processEvents()
    assert window.detail.title.text() == "Hotel am Stadtpark GmbH"
    window.detail.show_tab(DetailTab.VALIDATION)
    window.detail.validation.list.setFocus()
    window.escape()
    assert window.table.hasFocus() or not window.isActiveWindow()


def test_validation_jumps_to_missing_house_number_then_save_and_approve(window: MainWindow) -> None:
    window.table.select_order("order-003")
    validation = window.detail.validation
    item = next(
        validation.list.item(i)
        for i in range(validation.list.count())
        if isinstance(validation.list.item(i).data(ISSUE_ROLE), IssueView)
    )
    validation._activated(item)
    delivery = window.detail.delivery
    assert window.detail.tabs.currentWidget() is delivery
    number = delivery.edits["house_number"]
    assert number.property("state") == "missing"
    QTest.keyClicks(number, "1")
    assert window.detail.dirty
    assert window.detail.save_button.isEnabled()
    assert window.save()
    assert window.detail.snapshot is not None
    assert window.detail.snapshot.order.status is OrderStatus.READY
    window.approve()
    assert window.detail.snapshot.order.status is OrderStatus.APPROVED
    assert window.export_button.text() == "Exportieren (4)"


def test_export_dialog_summary_then_results(window: MainWindow) -> None:
    dialog = ExportDialog(window.workbench, window)
    texts = _texts(dialog)
    assert "3 Aufträge bereit" in texts
    assert "0 Aufträge mit Fehlern" in texts
    assert not dialog.live_mode.isEnabled()
    assert dialog.test_mode.isChecked()
    assert dialog.start.isEnabled()
    dialog._run()
    assert dialog.stack.currentIndex() == 1
    results = [dialog.results.item(r, 2).text() for r in range(dialog.results.rowCount())]
    assert results == ["Exportiert"] * 3
    dialog.close()


def test_layout_adapts_to_window_size(window: MainWindow, qapp: QApplication) -> None:
    window.resize(940, 640)
    qapp.processEvents()
    assert window.splitter.orientation() is Qt.Orientation.Vertical
    assert not window.table.isColumnHidden(Column.CONTACT)
    window.resize(1280, 680)
    qapp.processEvents()
    assert window.splitter.orientation() is Qt.Orientation.Horizontal
    assert window.table.columnWidth(Column.COMPANY) >= MIN_COMPANY_WIDTH - 2
    window.resize(1920, 1040)
    qapp.processEvents()
    assert not any(window.table.isColumnHidden(c) for c in Column)
