"""Auftragsliste: Modell, Filter und Tabelle."""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from ..app.presentation import OrderRow, Tone
from ..domain.status import OrderStatus
from .theme import TOKENS
from .widgets import StatusDelegate, tone_color

ROW_ROLE = Qt.ItemDataRole.UserRole + 2
SORT_ROLE = Qt.ItemDataRole.UserRole
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1


class Column(IntEnum):
    """Spalten in Anzeigereihenfolge."""

    STATUS = 0
    RECEIVED = 1
    NUMBER = 2
    COMPANY = 3
    CONTACT = 4
    POSITIONS = 5
    PROBLEMS = 6


HEADERS = (
    "Status",
    "Eingang",
    "Bestellnummer",
    "Firma",
    "Ansprechpartner",
    "Positionen",
    "Probleme",
)
HIDE_ORDER = (Column.CONTACT, Column.POSITIONS, Column.NUMBER)
MIN_COMPANY_WIDTH = 190


class OrderTableModel(QAbstractTableModel):
    """Zeilen der Auftragsliste."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[OrderRow] = []
        self._handlers: dict[int, Callable[[OrderRow, Column], object]] = {
            int(Qt.ItemDataRole.DisplayRole): self._display,
            int(SORT_ROLE): self._sort_key,
            int(STATUS_ROLE): lambda item, _column: item.status,
            int(ROW_ROLE): lambda item, _column: item,
            int(Qt.ItemDataRole.ToolTipRole): self._tooltip,
            int(Qt.ItemDataRole.ForegroundRole): self._foreground,
            int(Qt.ItemDataRole.TextAlignmentRole): self._alignment,
        }

    def set_rows(self, rows: list[OrderRow]) -> None:
        """Ersetzt alle Zeilen."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> OrderRow:
        """Zeile nach Index."""
        return self._rows[row]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        """Anzahl Aufträge."""
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        """Anzahl Spalten."""
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Spaltenüberschriften."""
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            right = section == Column.POSITIONS
            return int(
                (Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
                | Qt.AlignmentFlag.AlignVCenter
            )
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Inhalt, Sortierschlüssel, Farbe und Tooltip je Zelle."""
        handler = self._handlers.get(int(role))
        if not index.isValid() or handler is None:
            return None
        return handler(self._rows[index.row()], Column(index.column()))

    @staticmethod
    def _alignment(item: OrderRow, column: Column) -> int | None:
        if column is Column.POSITIONS:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    @staticmethod
    def _display(item: OrderRow, column: Column) -> str:
        values = {
            Column.STATUS: item.look.label,
            Column.RECEIVED: item.received_text,
            Column.NUMBER: item.order_number or "–",
            Column.COMPANY: item.company,
            Column.CONTACT: item.contact,
            Column.POSITIONS: str(item.positions) if item.positions else "–",
            Column.PROBLEMS: item.problems_text,
        }
        return values[column]

    @staticmethod
    def _sort_key(item: OrderRow, column: Column) -> object:
        if column is Column.STATUS:
            return item.look.priority
        if column is Column.RECEIVED:
            return item.received.timestamp() if item.received else 0.0
        if column is Column.POSITIONS:
            return item.positions
        if column is Column.PROBLEMS:
            return item.errors * 1000 + item.warnings
        return OrderTableModel._display(item, column).casefold()

    @staticmethod
    def _tooltip(item: OrderRow, column: Column) -> str | None:
        if column is Column.STATUS:
            return item.look.description
        if column is Column.PROBLEMS and item.first_problem:
            return item.first_problem
        if column is Column.COMPANY:
            return item.company
        return None

    @staticmethod
    def _foreground(item: OrderRow, column: Column) -> QColor | None:
        if column is Column.PROBLEMS and item.errors:
            return tone_color(Tone.DANGER)
        if column is Column.PROBLEMS and item.warnings:
            return tone_color(Tone.WARNING)
        if column is Column.NUMBER and not item.order_number:
            return QColor(TOKENS.text_disabled)
        return None


class OrderFilterModel(QSortFilterProxyModel):
    """Filter nach Bereich und Suchtext."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._statuses: frozenset[OrderStatus] = frozenset()
        self._query = ""
        self.setSortRole(SORT_ROLE)

    def set_statuses(self, statuses: frozenset[OrderStatus]) -> None:
        """Bereich wählen."""
        self.beginFilterChange()
        self._statuses = statuses
        self.endFilterChange()

    def set_query(self, query: str) -> None:
        """Suchtext setzen."""
        self.beginFilterChange()
        self._query = query.strip()
        self.endFilterChange()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        """Zeile sichtbar, wenn Bereich und Suche passen."""
        model = self.sourceModel()
        if not isinstance(model, OrderTableModel):
            return True
        item = model.row_at(source_row)
        return (not self._statuses or item.status in self._statuses) and item.matches(self._query)


class OrderTableView(QTableView):
    """Kompakte Auftragstabelle; Enter öffnet den Auftrag."""

    open_requested = Signal()
    order_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = OrderTableModel(self)
        self.proxy = OrderFilterModel(self)
        self.proxy.setSourceModel(self.source)
        self.setModel(self.proxy)
        self.setItemDelegateForColumn(Column.STATUS, StatusDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setSortingEnabled(True)
        self.setTabKeyNavigation(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(TOKENS.row_height)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(48)
        header.setStretchLastSection(False)
        for column in Column:
            mode = (
                QHeaderView.ResizeMode.Stretch
                if column is Column.COMPANY
                else QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(column, mode)
        self.sortByColumn(Column.RECEIVED, Qt.SortOrder.DescendingOrder)
        self.selectionModel().currentRowChanged.connect(self._current_changed)
        self.setAccessibleName("Auftragsliste")

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if current.isValid():
            item = current.data(ROW_ROLE)
            if isinstance(item, OrderRow):
                self.order_selected.emit(item.order_id)

    def current_order_id(self) -> str | None:
        """ID des markierten Auftrags."""
        index = self.currentIndex()
        item = index.data(ROW_ROLE) if index.isValid() else None
        return item.order_id if isinstance(item, OrderRow) else None

    def select_order(self, order_id: str | None) -> bool:
        """Markiert einen Auftrag, wenn er sichtbar ist; sonst den ersten."""
        for row_number in range(self.proxy.rowCount()):
            index = self.proxy.index(row_number, 0)
            item = index.data(ROW_ROLE)
            if isinstance(item, OrderRow) and item.order_id == order_id:
                self.selectionModel().setCurrentIndex(
                    index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self.scrollTo(index)
                return True
        if self.proxy.rowCount():
            first = self.proxy.index(0, 0)
            flags = (
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows
            )
            self.selectionModel().setCurrentIndex(first, flags)
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Enter öffnet den markierten Auftrag in der Detailansicht."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.open_requested.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Passt die Spalten an die Breite an."""
        super().resizeEvent(event)
        self.fit_columns()

    def fit_columns(self) -> None:
        """Firma behält mindestens ``MIN_COMPANY_WIDTH``; sonst entfallen Spalten nach Priorität."""
        available = self.viewport().width()
        header = self.horizontalHeader()

        def wanted(column: Column) -> int:
            return max(self.sizeHintForColumn(column), header.sectionSizeHint(column))

        visible: list[Column] = [c for c in Column if c is not Column.COMPANY]
        for column in HIDE_ORDER:
            self.setColumnHidden(column, False)
        for column in HIDE_ORDER:
            if sum(wanted(c) for c in visible) + MIN_COMPANY_WIDTH <= available:
                break
            self.setColumnHidden(column, True)
            visible.remove(column)
