"""Detailansicht eines Auftrags mit acht Bereichen."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app.presentation import (
    DetailTab,
    IssueView,
    MatchView,
    Tone,
    format_date,
    format_money,
    format_quantity,
    plural,
    present_findings,
    present_match,
)
from ..app.workbench import OrderSnapshot, Workbench
from ..domain.findings import Severity
from ..domain.models import (
    COMPANY_OR_NAME,
    Address,
    ArticleMatch,
    MatchStatus,
    MatchStrategy,
    Order,
    OrderLine,
    PaymentMethod,
)
from ..domain.provenance import Field
from ..domain.status import ExportMode, OrderStatus
from .theme import TOKENS, TONES
from .widgets import (
    SEVERITY_GLYPH,
    SEVERITY_TONE,
    ElidedLabel,
    Notice,
    StatusChip,
    muted,
    paint_glyph,
    row,
    section_label,
    set_field_state,
)

FORM_MAX_WIDTH = 720
SHORT_LABELS = {
    DetailTab.INVOICE: "Rechnung",
    DetailTab.DELIVERY: "Lieferung",
    DetailTab.SHIPPING: "Versand",
    DetailTab.MAIL: "Mail",
}
TAB_PADDING = 18
HEADER_WRAP_WIDTH = 640


class InputError(ValueError):
    """Eingabe lässt sich nicht übernehmen; Meldung für den Benutzer."""


def parse_decimal(text: str, label: str) -> Decimal | None:
    """Deutsche Zahl, etwa ``2,5`` oder ``1.234,50``."""
    cleaned = text.strip().replace("€", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned.replace(".", "").replace(",", "."))
    except InvalidOperation as exc:
        raise InputError(f"{label}: „{text}“ ist keine gültige Zahl") from exc


def parse_date(text: str, label: str) -> date | None:
    """Datum im Format TT.MM.JJJJ."""
    if not text.strip():
        return None
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise InputError(f"{label}: Datum bitte als TT.MM.JJJJ eingeben") from exc


def _changed[T](field: Field[T], value: T | None) -> Field[T]:
    return field if value == field.value else Field.manual(value)


class Page(QScrollArea):
    """Bereich mit scrollbarem, in der Breite begrenztem Formular."""

    edited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        outer = QHBoxLayout(holder)
        outer.setContentsMargins(16, 14, 16, 16)
        self.body = QWidget()
        self.body.setMaximumWidth(FORM_MAX_WIDTH)
        self.layout_ = QVBoxLayout(self.body)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(10)
        outer.addWidget(self.body, 1)
        outer.addStretch(0)
        self.setWidget(holder)
        self.loading = False

    @staticmethod
    def form() -> QFormLayout:
        """Formular im Windows-Stil: Beschriftung links, Felder dehnen sich."""
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(8)
        return layout

    def notify(self) -> None:
        """Meldet eine Benutzeränderung (nicht beim Laden)."""
        if not self.loading:
            self.edited.emit()

    def line(self, placeholder: str = "") -> QLineEdit:
        """Eingabefeld, das Änderungen meldet."""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.textEdited.connect(lambda _text: self.notify())
        return edit


class OverviewPage(Page):
    """Auftragskopf und Zusammenfassung."""

    show_validation = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.notice = Notice()
        self.to_validation = QPushButton("Zur Validierung")
        self.to_validation.setProperty("role", "link")
        self.to_validation.clicked.connect(self.show_validation.emit)
        self.layout_.addWidget(self.notice)
        self.layout_.addWidget(self.to_validation, 0, Qt.AlignmentFlag.AlignLeft)
        form = self.form()
        self.reference = self.line("Bestellnummer des Kunden")
        self.values: dict[str, QLabel] = {}
        for key, label in (
            ("document", "Belegnummer"),
            ("order_date", "Bestelldatum"),
            ("company", "Kunde"),
            ("contact", "Ansprechpartner"),
            ("sender", "Absender"),
            ("received", "Eingang"),
            ("positions", "Positionen"),
            ("value", "Warenwert netto"),
        ):
            self.values[key] = muted()
            self.values[key].setProperty("muted", False)
            form.addRow(label, self.values[key])
            if key == "document":
                form.addRow("Bestellnummer", self.reference)
        self.layout_.addWidget(section_label("Auftrag"))
        self.layout_.addLayout(form)
        self.layout_.addStretch(1)

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Füllt die Übersicht."""
        self.loading = True
        order, rowdata = snapshot.order, snapshot.row
        errors = (
            snapshot.validation.errors(order.acknowledged)
            if order.status in (OrderStatus.NEW, OrderStatus.NEEDS_REVIEW, OrderStatus.READY)
            else ()
        )
        self.reference.setText(order.customer_reference.value or "")
        self.reference.setReadOnly(not snapshot.editable)
        mail = snapshot.mail.metadata if snapshot.mail else None
        values = {
            "document": order.document_number or "wird bei Freigabe vergeben",
            "order_date": format_date(order.order_date.value),
            "company": rowdata.company or "–",
            "contact": rowdata.contact or "–",
            "sender": f"{mail.sender_name} <{mail.sender}>" if mail else "–",
            "received": f"{mail.date_header:%d.%m.%Y %H:%M}" if mail and mail.date_header else "–",
            "positions": str(len(order.lines)),
            "value": format_money(self._net_value(order)),
        }
        for key, text in values.items():
            self.values[key].setText(text)
        tone, text = self._notice(snapshot, len(errors))
        self.notice.show_text(tone, text)
        self.to_validation.setVisible(bool(errors) or bool(snapshot.validation.warnings()))
        self.loading = False

    @staticmethod
    def _net_value(order: Order) -> Decimal | None:
        total = Decimal(0)
        for line in order.lines:
            price = line.unit_price.value or (
                line.match.article.price if line.match.article else None
            )
            if price is None or line.quantity.value is None:
                return None
            total += price * line.quantity.value
        return total

    @staticmethod
    def _notice(snapshot: OrderSnapshot, errors: int) -> tuple[Tone, str]:
        order = snapshot.order
        if order.status in (OrderStatus.NEW, OrderStatus.NEEDS_REVIEW) and errors:
            return (
                Tone.WARNING,
                f"{plural(errors, 'Punkt erfordert', 'Punkte erfordern')} eine Benutzeraktion.",
            )
        number = order.document_number or ""
        texts = {
            OrderStatus.READY: (
                Tone.SUCCESS,
                "Vollständig geprüft. Mit „Freigeben“ wird die Belegnummer vergeben.",
            ),
            OrderStatus.APPROVED: (
                Tone.INFO,
                f"Freigegeben als {number}. Der Auftrag wartet auf den Export.",
            ),
            OrderStatus.EXPORTED: (Tone.SUCCESS, f"Exportiert als {number}."),
            OrderStatus.FAILED: (
                Tone.DANGER,
                "Der letzte Export ist fehlgeschlagen. Details im Bereich „Export“.",
            ),
            OrderStatus.UNCLEAR: (Tone.DANGER, "Exportstatus unklar. Bitte in Lexware prüfen."),
            OrderStatus.IGNORED: (Tone.INFO, "Als keine Bestellung markiert."),
        }
        return texts.get(order.status, (Tone.INFO, ""))

    def apply(self, order: Order) -> Order:
        """Übernimmt die Bestellnummer."""
        text = self.reference.text().strip() or None
        return replace(order, customer_reference=_changed(order.customer_reference, text))


ADDRESS_FIELDS = (
    ("company", "Firma"),
    ("department", "Abteilung"),
    ("name", "Name"),
    ("street", "Straße"),
    ("house_number", "Hausnummer"),
    ("postal_code", "PLZ"),
    ("city", "Ort"),
    ("country", "Land"),
    ("phone", "Telefon"),
    ("email", "E-Mail"),
)


class AddressPage(Page):
    """Rechnungs- oder Lieferadresse."""

    def __init__(self, delivery: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.delivery = delivery
        self.edits = {key: self.line() for key, _ in ADDRESS_FIELDS}
        self.edits["house_number"].setMaximumWidth(90)
        self.edits["postal_code"].setMaximumWidth(90)
        self.edits["country"].setMaximumWidth(60)
        self.edits["country"].setMaxLength(2)
        self.edits["country"].setPlaceholderText("DE")
        self.same = QCheckBox("Entspricht der Rechnungsadresse")
        self.same.toggled.connect(self._same_toggled)
        if delivery:
            self.layout_.addWidget(self.same)
        self.reason = Notice()
        self.layout_.addWidget(self.reason)
        form = self.form()
        e = self.edits
        form.addRow("Firma", e["company"])
        form.addRow("Abteilung", e["department"])
        form.addRow("Name", e["name"])
        form.addRow("Straße, Nr.", row(e["street"], e["house_number"], stretch=(1, 0)))
        form.addRow("PLZ, Ort", row(e["postal_code"], e["city"], stretch=(0, 1)))
        form.addRow("Land", e["country"])
        form.addRow("Telefon", e["phone"])
        form.addRow("E-Mail", e["email"])
        self.layout_.addLayout(form)
        self.layout_.addStretch(1)
        self._field: Field[Address] = Field()

    def _same_toggled(self, checked: bool) -> None:
        for edit in self.edits.values():
            edit.setEnabled(not checked)
        self.notify()

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Füllt die Felder und markiert fehlende oder unsichere Angaben."""
        self.loading = True
        order = snapshot.order
        self._field = order.delivery_address if self.delivery else order.invoice_address
        address = self._field.value or Address()
        for key, edit in self.edits.items():
            edit.setText(getattr(address, key))
            edit.setReadOnly(not snapshot.editable)
            set_field_state(edit, "")
        self.same.setChecked(self.delivery and order.delivery_same_as_invoice)
        self.same.setEnabled(snapshot.editable)
        for edit in self.edits.values():
            edit.setEnabled(not self.same.isChecked())
        if not self.same.isChecked():
            self._mark(address)
        self.loading = False

    def _mark(self, address: Address) -> None:
        if self._field.needs_attention and self._field.value is not None:
            reason = self._field.evidence.describe() if self._field.evidence else "Angabe unsicher"
            for key in ("company", "name", "street", "city"):
                set_field_state(self.edits[key], "review", reason)
            self.reason.show_text(Tone.WARNING, f"Bitte prüfen: {reason}")
        else:
            self.reason.hide()
        for missing in address.missing_fields():
            keys = ("company", "name") if missing == COMPANY_OR_NAME else (missing,)
            for key in keys:
                if key in self.edits:
                    set_field_state(self.edits[key], "missing", "Pflichtangabe fehlt")

    def focus_problem(self) -> None:
        """Setzt den Fokus auf das erste markierte Feld."""
        for key, _ in ADDRESS_FIELDS:
            if self.edits[key].property("state") in ("missing", "review"):
                self.edits[key].setFocus()
                return
        self.edits["company"].setFocus()

    def apply(self, order: Order) -> Order:
        """Übernimmt die Adresse."""
        if self.delivery and self.same.isChecked():
            return replace(order, delivery_same_as_invoice=True)
        base = self._field.value or Address()
        values = {key: edit.text().strip() for key, edit in self.edits.items()}
        values["country"] = values["country"].upper()
        address = replace(base, **values)
        updated = _changed(self._field, address if any(values.values()) else None)
        if self.delivery:
            return replace(order, delivery_address=updated, delivery_same_as_invoice=False)
        return replace(order, invoice_address=updated)


LINE_HEADERS = ("Pos.", "Artikelnr.", "Bezeichnung", "Menge", "Einheit", "Einzelpreis", "Zuordnung")
ARTICLE_COLUMN, QUANTITY_COLUMN = 1, 3
MATCH_LABELS = {
    MatchStatus.MATCHED: "automatisch",
    MatchStatus.MANUAL: "manuell",
    MatchStatus.NEEDS_REVIEW: "prüfen",
    MatchStatus.UNKNOWN: "fehlt",
}


class MatchPanel(QFrame):
    """Zuordnung der markierten Position mit Vorschlägen bei unsicheren Treffern."""

    accepted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MatchPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(section_label("Zuordnung der markierten Position"))
        form = Page.form()
        self.values: dict[str, QLabel] = {}
        for key, label in (
            ("source", "Quelle"),
            ("article_number", "Artikelnummer"),
            ("name", "Name"),
            ("method", "Match-Methode"),
            ("certainty", "Match-Sicherheit"),
            ("status", "Status"),
        ):
            value = muted()
            value.setProperty("muted", False)
            value.setWordWrap(True)
            self.values[key] = value
            form.addRow(label, value)
        layout.addLayout(form)
        self.notice = Notice()
        layout.addWidget(self.notice)
        self.candidates = QListWidget()
        self.candidates.setMaximumHeight(96)
        self.candidates.setAccessibleName("Vorschläge")
        self.candidates.itemDoubleClicked.connect(lambda _item: self._accept())
        layout.addWidget(self.candidates)
        self.accept_button = QPushButton("Vorschlag übernehmen")
        self.accept_button.clicked.connect(self._accept)
        layout.addWidget(self.accept_button, 0, Qt.AlignmentFlag.AlignLeft)

    def show_view(self, view: MatchView | None, editable: bool) -> None:
        """Zeigt die Zuordnung; ohne Position bleibt das Feld leer."""
        self.setVisible(view is not None)
        if view is None:
            return
        for key, label in self.values.items():
            label.setText(str(getattr(view, key)))
        self.values["source"].setText(f"{view.source} · {view.catalog}")
        self.values["status"].setStyleSheet(f"color: {TONES[view.tone][0]}; font-weight: 600;")
        if view.needs_action:
            self.notice.show_text(view.tone, f"{view.status}: {view.reason}")
        else:
            self.notice.hide()
        self.candidates.clear()
        for candidate in view.candidates:
            item = QListWidgetItem(
                f"{candidate.article.number} · {candidate.article.name} "
                f"({candidate.method}, {candidate.score})"
            )
            item.setData(Qt.ItemDataRole.UserRole, candidate.article)
            self.candidates.addItem(item)
        has_candidates = bool(view.candidates)
        self.candidates.setVisible(has_candidates)
        self.accept_button.setVisible(has_candidates and editable)
        if has_candidates:
            self.candidates.setCurrentRow(0)

    def _accept(self) -> None:
        item = self.candidates.currentItem()
        article = item.data(Qt.ItemDataRole.UserRole) if item else None
        if article is not None and self.accept_button.isVisible():
            self.accepted.emit(article)


class LinesPage(QWidget):
    """Bestellpositionen mit Zuordnungsdetails."""

    edited = Signal()
    candidate_accepted = Signal(int, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.hint = muted("Artikelnummer und Menge lassen sich direkt in der Tabelle ändern (F2).")
        layout.addWidget(self.hint)
        self.table = QTableWidget(0, len(LINE_HEADERS))
        self.table.setHorizontalHeaderLabels(list(LINE_HEADERS))
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        header = self.table.horizontalHeader()
        for column in range(len(LINE_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemChanged.connect(self._item_changed)
        self.table.setMinimumHeight(TOKENS.row_height * 5)
        self.match_panel = MatchPanel()
        self.match_panel.accepted.connect(
            lambda article: self.candidate_accepted.emit(self.table.currentRow(), article)
        )
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setWidget(self.match_panel)
        panel_scroll.setMinimumHeight(TOKENS.row_height * 4)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(panel_scroll)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)
        self.table.currentCellChanged.connect(lambda row, _c, _pr, _pc: self._show_match(row))
        self._editable = False
        self._order: Order | None = None
        self._loading = False
        self._workbench: Workbench | None = None

    def _item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._loading:
            self.edited.emit()

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Füllt die Tabelle und hebt Positionen mit Befunden hervor."""
        self._loading = True
        self._order, self._workbench = snapshot.order, workbench
        issues = {
            v.line_index: v
            for v in present_findings(snapshot.validation, snapshot.order)
            if v.line_index is not None and v.severity is not Severity.INFO
        }
        self.table.setRowCount(len(snapshot.order.lines))
        for index, line in enumerate(snapshot.order.lines):
            article = line.match.article
            price = line.unit_price.value or (article.price if article else None)
            cells = (
                str(line.position),
                article.number if article else (line.article_hint.value or ""),
                line.description,
                format_quantity(line.quantity.value),
                line.unit or (article.unit if article else ""),
                format_money(price),
                MATCH_LABELS[line.match.status],
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                editable = snapshot.editable and column in (ARTICLE_COLUMN, QUANTITY_COLUMN)
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                item.setFlags(flags | Qt.ItemFlag.ItemIsEditable if editable else flags)
                if column in (0, QUANTITY_COLUMN, 5):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                issue = issues.get(index)
                if issue is not None:
                    tone = SEVERITY_TONE[issue.severity]
                    item.setBackground(QColor(TONES[tone][1]))
                    item.setToolTip(
                        f"{issue.title}: {issue.detail}" if issue.detail else issue.title
                    )
                self.table.setItem(index, column, item)
        self.hint.setVisible(snapshot.editable)
        self._editable = snapshot.editable
        self._loading = False
        if snapshot.order.lines:
            first = next(
                (
                    i
                    for i, line in enumerate(snapshot.order.lines)
                    if line.match.status in (MatchStatus.NEEDS_REVIEW, MatchStatus.UNKNOWN)
                ),
                0,
            )
            self.table.setCurrentCell(first, 0)
        self._show_match(self.table.currentRow())

    def _show_match(self, row: int) -> None:
        order = self._order
        line = order.lines[row] if order is not None and 0 <= row < len(order.lines) else None
        self.match_panel.show_view(present_match(line) if line else None, self._editable)

    def focus_line(self, index: int | None, issue: IssueView | None = None) -> None:
        """Markiert eine Position und öffnet das betroffene Feld zur Bearbeitung."""
        if index is None or index >= self.table.rowCount():
            self.table.setFocus()
            return
        column = QUANTITY_COLUMN if issue and "Menge" in issue.location else ARTICLE_COLUMN
        self.table.setCurrentCell(index, column)
        self.table.setFocus()
        item = self.table.item(index, column)
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.table.editItem(item)

    def apply(self, order: Order) -> Order:
        """Übernimmt Artikelnummern und Mengen."""
        lines = []
        for index, original in enumerate(order.lines):
            number_item = self.table.item(index, ARTICLE_COLUMN)
            quantity_item = self.table.item(index, QUANTITY_COLUMN)
            if number_item is None or quantity_item is None:
                lines.append(original)
                continue
            quantity = parse_decimal(quantity_item.text(), f"Position {original.position}, Menge")
            if quantity is not None and quantity <= 0:
                raise InputError(f"Position {original.position}: Menge muss größer als 0 sein")
            updated = replace(original, quantity=_changed(original.quantity, quantity))
            number = number_item.text().strip()
            article = original.match.article
            current = article.number if article else (original.article_hint.value or "")
            lines.append(self._assign(updated, number) if number != current else updated)
        return replace(order, lines=tuple(lines))

    def _assign(self, line: OrderLine, number: str) -> OrderLine:
        article = self._workbench.article(number) if self._workbench and number else None
        if article is not None:
            match = ArticleMatch(
                MatchStatus.MANUAL, article, MatchStrategy.MANUAL, "Vom Benutzer zugeordnet"
            )
            return replace(line, match=match, unit=line.unit or article.unit)
        reason = f"„{number}“ ist nicht im Katalog" if number else "Keine Artikelnummer"
        return replace(
            line,
            match=ArticleMatch(MatchStatus.UNKNOWN, None, MatchStrategy.NONE, reason),
            article_hint=Field.manual(number or None),
        )


class ShippingPage(Page):
    """Versand und Zahlung."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = self.form()
        self.payment = QComboBox()
        self.payment.addItem("Nicht angegeben", None)
        for method in PaymentMethod:
            self.payment.addItem(method.label, method.value)
        self.payment.currentIndexChanged.connect(lambda _index: self.notify())
        self.shipping = self.line("z. B. Tourlieferung, Spedition")
        self.fee = self.line("0,00")
        self.fee.setMaximumWidth(120)
        self.requested = self.line("TT.MM.JJJJ")
        self.requested.setMaximumWidth(120)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Bemerkung zum Auftrag")
        self.note.setMaximumHeight(90)
        self.note.setTabChangesFocus(True)
        self.note.textChanged.connect(self.notify)
        form.addRow("Zahlungsart", self.payment)
        form.addRow("Versandart", self.shipping)
        form.addRow("Versandkosten (€)", self.fee)
        form.addRow("Lieferwunsch", self.requested)
        form.addRow("Bemerkung", self.note)
        self.layout_.addWidget(section_label("Versand & Zahlung"))
        self.layout_.addLayout(form)
        self.layout_.addStretch(1)

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Füllt die Felder."""
        self.loading = True
        order = snapshot.order
        self.payment.setCurrentIndex(
            max(
                0,
                self.payment.findData(
                    order.payment_method.value.value if order.payment_method.value else None
                ),
            )
        )
        self.shipping.setText(order.shipping_method.value or "")
        fee = order.shipping_fee.value
        self.fee.setText(format_money(fee).replace(" €", "") if fee is not None else "")
        self.requested.setText(format_date(order.requested_delivery.value).replace("–", ""))
        self.note.setPlainText(order.note)
        for widget in (self.shipping, self.fee, self.requested):
            widget.setReadOnly(not snapshot.editable)
        self.note.setReadOnly(not snapshot.editable)
        self.payment.setEnabled(snapshot.editable)
        state = (
            "review"
            if order.payment_method.needs_attention
            else ("missing" if order.payment_method.value is None else "")
        )
        set_field_state(self.payment, state, "Zahlungsart prüfen" if state else "")
        self.loading = False

    def apply(self, order: Order) -> Order:
        """Übernimmt Versand und Zahlung."""
        data = self.payment.currentData()
        method = PaymentMethod(data) if isinstance(data, str) and data else None
        return replace(
            order,
            payment_method=_changed(order.payment_method, method),
            shipping_method=_changed(order.shipping_method, self.shipping.text().strip() or None),
            shipping_fee=_changed(
                order.shipping_fee, parse_decimal(self.fee.text(), "Versandkosten")
            ),
            requested_delivery=_changed(
                order.requested_delivery, parse_date(self.requested.text(), "Lieferwunsch")
            ),
            note=self.note.toPlainText().strip(),
        )


class MailPage(QWidget):
    """Originalmail, ausschließlich als Text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        form = Page.form()
        self.from_label, self.subject, self.received = muted(), muted(), muted()
        for label, widget in (
            ("Von", self.from_label),
            ("Betreff", self.subject),
            ("Eingang", self.received),
        ):
            widget.setProperty("muted", False)
            form.addRow(label, widget)
        layout.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setProperty("readOnly", True)
        self.body.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.body.setTabChangesFocus(True)
        layout.addWidget(self.body, 1)

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Zeigt Kopf und Text der Mail."""
        mail = snapshot.mail.metadata if snapshot.mail else None
        self.from_label.setText(f"{mail.sender_name} <{mail.sender}>" if mail else "–")
        self.subject.setText(mail.subject if mail else "–")
        self.received.setText(
            f"{mail.date_header:%d.%m.%Y %H:%M}" if mail and mail.date_header else "–"
        )
        self.body.setPlainText(workbench.mail_text(snapshot) or "Keine Mail gespeichert.")


ISSUE_ROLE = Qt.ItemDataRole.UserRole + 10
GROUP_ROLE = Qt.ItemDataRole.UserRole + 11
GROUPS = (
    (Severity.ERROR, "Muss behoben werden"),
    (Severity.WARNING, "Bitte prüfen"),
    (Severity.INFO, "Hinweise"),
)


class IssueDelegate(QStyledItemDelegate):
    """Befund zweizeilig: Titel, darunter „→ Ort → Detail → Aktion“."""

    def _lines(self, option: QStyleOptionViewItem) -> tuple[int, int]:
        height = option.fontMetrics.height()
        return height, height * 2 + 14

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Gruppen einzeilig, Befunde zweizeilig."""
        line, double = self._lines(option)
        group = index.data(GROUP_ROLE)
        return QSize(200, line + 14 if group else double)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Zeichnet Gruppe oder Befund."""
        self.initStyleOption(option, index)
        rect, metrics = option.rect, option.fontMetrics
        group = index.data(GROUP_ROLE)
        painter.save()
        if group:
            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(TOKENS.text_secondary))
            painter.drawText(
                rect.adjusted(10, 6, -8, 0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                str(group),
            )
            painter.restore()
            return
        view = index.data(ISSUE_ROLE)
        option.text = ""
        widget = option.widget
        (widget.style() if widget else QApplication.style()).drawControl(
            QStyle.ControlElement.CE_ItemViewItem, option, painter, widget
        )
        if isinstance(view, IssueView):
            line, _ = self._lines(option)
            tone = SEVERITY_TONE[view.severity]
            top = rect.top() + 6
            paint_glyph(
                painter,
                QPointF(rect.left() + 18, top + line / 2),
                SEVERITY_GLYPH[view.severity],
                QColor(TONES[tone][0]),
            )
            text_left = rect.left() + 32
            width = rect.right() - text_left - 8
            font = QFont(option.font)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(TOKENS.text))
            title = QFontMetrics(font).elidedText(view.title, Qt.TextElideMode.ElideRight, width)
            painter.drawText(
                text_left,
                top,
                width,
                line,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                title,
            )
            painter.setFont(option.font)
            x = text_left
            for position, step in enumerate(view.steps):
                last = position == len(view.steps) - 1
                painter.setPen(QColor(TONES[tone][0] if last else TOKENS.text_secondary))
                text = f"→ {step}"
                available = rect.right() - x - 8
                if available < 40:
                    break
                shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available)
                painter.drawText(
                    x,
                    top + line + 2,
                    available,
                    line,
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    shown,
                )
                x += metrics.horizontalAdvance(shown) + 14
        painter.restore()


class ValidationPage(QWidget):
    """Befunde: was fehlt, wo, und was zu tun ist."""

    issue_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.notice = Notice()
        layout.addWidget(self.notice)
        self.list = QListWidget()
        self.list.setItemDelegate(IssueDelegate(self.list))
        self.list.setUniformItemSizes(False)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setAccessibleName("Befunde")
        self.list.itemActivated.connect(self._activated)
        self.list.itemDoubleClicked.connect(self._activated)
        layout.addWidget(self.list, 1)
        self.jump = QPushButton("Zur Stelle springen")
        self.jump.setToolTip("Enter oder Doppelklick auf einen Befund")
        self.jump.clicked.connect(lambda: self._activated(self.list.currentItem()))
        layout.addWidget(self.jump, 0, Qt.AlignmentFlag.AlignRight)

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Gruppiert die Befunde nach Dringlichkeit."""
        self.list.clear()
        views = present_findings(snapshot.validation, snapshot.order)
        first: QListWidgetItem | None = None
        for severity, title in GROUPS:
            members = [v for v in views if v.severity is severity]
            if not members:
                continue
            group = QListWidgetItem()
            group.setData(GROUP_ROLE, f"{title} ({len(members)})")
            group.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(group)
            for view in members:
                item = QListWidgetItem()
                item.setData(ISSUE_ROLE, view)
                item.setData(
                    Qt.ItemDataRole.AccessibleTextRole, f"{view.title}. " + ". ".join(view.steps)
                )
                item.setToolTip("\n".join((view.title, *(f"→ {step}" for step in view.steps))))
                self.list.addItem(item)
                first = first or item
        self.list.setVisible(bool(views))
        self.jump.setVisible(bool(views))
        if views:
            errors = sum(1 for v in views if v.severity is Severity.ERROR)
            tone = Tone.WARNING if errors else Tone.INFO
            self.notice.show_text(
                tone, f"{plural(len(views), 'Befund', 'Befunde')}, davon {errors} blockierend."
            )
            if first is not None:
                self.list.setCurrentItem(first)
        else:
            self.notice.show_text(
                Tone.SUCCESS, "Keine offenen Punkte. Der Auftrag ist vollständig."
            )

    def _activated(self, item: QListWidgetItem | None) -> None:
        view = item.data(ISSUE_ROLE) if item else None
        if isinstance(view, IssueView):
            self.issue_activated.emit(view)


EXPORT_HISTORY_HEADERS = ("Zeitpunkt", "Modus", "Ergebnis", "Datei")
MODE_TEXT = {ExportMode.LIVE: "Produktiv", ExportMode.TEST: "Test", ExportMode.DRY_RUN: "Probelauf"}
RESULT_TEXT = {
    "success": "Erfolgreich",
    "blocked": "Nicht ausgeführt",
    "failed": "Fehlgeschlagen",
    "unclear": "Unklar",
    "recovered": "Wiederhergestellt",
}


class ExportPage(QWidget):
    """Exportvorschau und bisherige Exportversuche dieses Auftrags."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.notice = Notice()
        layout.addWidget(self.notice)
        form = Page.form()
        self.file_name, self.target = muted(), muted()
        form.addRow("Dateiname", self.file_name)
        form.addRow("Exportziel", self.target)
        layout.addLayout(form)
        layout.addWidget(section_label("XML-Vorschau"))
        self.xml = QPlainTextEdit()
        self.xml.setReadOnly(True)
        self.xml.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.xml.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.xml.setTabChangesFocus(True)
        layout.addWidget(self.xml, 3)
        layout.addWidget(section_label("Bisherige Exporte"))
        self.history = QTableWidget(0, len(EXPORT_HISTORY_HEADERS))
        self.history.setHorizontalHeaderLabels(list(EXPORT_HISTORY_HEADERS))
        self.history.verticalHeader().hide()
        self.history.setShowGrid(False)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.horizontalHeader().setStretchLastSection(True)
        self.history.setMaximumHeight(140)
        self.history.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.history, 1)
        self._snapshot: OrderSnapshot | None = None
        self._workbench: Workbench | None = None

    def load(self, snapshot: OrderSnapshot, workbench: Workbench) -> None:
        """Merkt den Auftrag; die Vorschau entsteht erst beim Anzeigen."""
        self._snapshot, self._workbench = snapshot, workbench
        self.xml.clear()
        profile = workbench.profile
        target = (
            "Lexware-Importordner"
            if workbench.production_allowed
            else "Testordner (Produktivexport gesperrt)"
        )
        self.target.setText(f"{target} · Profil „{profile.name}“")
        jobs = workbench.export_history(snapshot.order.id)
        self.history.setRowCount(len(jobs))
        for index, job in enumerate(reversed(jobs)):
            when = job.finished_at or job.created_at
            cells = (
                when[:16].replace("T", " "),
                MODE_TEXT.get(job.mode, job.mode.value),
                RESULT_TEXT.get(job.result, job.result or "–"),
                job.file_name or "–",
            )
            for column, text in enumerate(cells):
                self.history.setItem(index, column, QTableWidgetItem(text))
        self.history.setVisible(bool(jobs))

    def refresh_preview(self) -> None:
        """Erzeugt die XML-Vorschau mit allen Befunden."""
        if self._snapshot is None or self._workbench is None:
            return
        preview = self._workbench.preview(self._snapshot.order)
        self.file_name.setText(preview.file_name or "–")
        self.xml.setPlainText(preview.xml or "")
        errors = preview.validation.errors()
        if errors:
            self.notice.show_text(
                Tone.DANGER, "Export nicht möglich: " + "; ".join(e.message for e in errors[:3])
            )
        elif self._snapshot.order.status is OrderStatus.APPROVED:
            self.notice.show_text(
                Tone.SUCCESS, "Exportbereit. Der Export startet über „Exportieren“ (Strg+E)."
            )
        else:
            self.notice.show_text(
                Tone.INFO, "Vorschau. Exportiert werden nur freigegebene Aufträge."
            )


class DetailView(QWidget):
    """Kopfzeile mit Aktionen und die acht Bereiche."""

    save_requested = Signal()
    approve_requested = Signal()
    ignore_requested = Signal()
    dirty_changed = Signal(bool)
    candidate_accepted = Signal(int, object)

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.snapshot: OrderSnapshot | None = None
        self.dirty = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        empty = QLabel("Kein Auftrag ausgewählt")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setProperty("muted", True)
        self.stack.addWidget(empty)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())
        self.tabs = QTabWidget()
        self.tabs.setObjectName("Detail")
        self.tabs.setDocumentMode(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.overview, self.invoice, self.delivery = (
            OverviewPage(),
            AddressPage(False),
            AddressPage(True),
        )
        self.lines, self.shipping, self.mail = LinesPage(), ShippingPage(), MailPage()
        self.validation, self.export = ValidationPage(), ExportPage()
        self.pages: dict[DetailTab, QWidget] = {
            DetailTab.OVERVIEW: self.overview,
            DetailTab.INVOICE: self.invoice,
            DetailTab.DELIVERY: self.delivery,
            DetailTab.LINES: self.lines,
            DetailTab.SHIPPING: self.shipping,
            DetailTab.MAIL: self.mail,
            DetailTab.VALIDATION: self.validation,
            DetailTab.EXPORT: self.export,
        }
        self._compact = False
        self._open_issues = 0
        for tab, page in self.pages.items():
            self.tabs.addTab(page, tab.value.replace("&", "&&"))
            self.tabs.setTabToolTip(self.tabs.indexOf(page), tab.value)
        for page in (self.overview, self.invoice, self.delivery, self.lines, self.shipping):
            page.edited.connect(self._mark_dirty)
        self.overview.show_validation.connect(lambda: self.show_tab(DetailTab.VALIDATION))
        self.validation.issue_activated.connect(self.navigate)
        self.lines.candidate_accepted.connect(self.candidate_accepted.emit)
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        self.stack.addWidget(content)

    def _header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("DetailHeader")
        outer = QVBoxLayout(header)
        outer.setContentsMargins(16, 10, 12, 10)
        outer.setSpacing(8)
        layout = QHBoxLayout()
        layout.setSpacing(10)
        outer.addLayout(layout)
        self._top_row = layout
        self._bottom_row = QHBoxLayout()
        outer.addLayout(self._bottom_row)
        self._actions_below = False
        self.action_bar = QWidget()
        actions = QHBoxLayout(self.action_bar)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = ElidedLabel()
        self.title.setObjectName("DetailTitle")
        self.subtitle = ElidedLabel()
        self.subtitle.setProperty("muted", True)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        layout.addLayout(titles, 1)
        self.chip = StatusChip()
        layout.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.ignore_button = QPushButton("Keine Bestellung")
        self.ignore_button.setToolTip("Mail als keine Bestellung markieren")
        self.save_button = QPushButton("Speichern")
        self.save_button.setToolTip("Änderungen speichern (Strg+S)")
        self.approve_button = QPushButton("Freigeben")
        self.approve_button.setProperty("role", "primary")
        for button, signal in (
            (self.ignore_button, self.ignore_requested),
            (self.save_button, self.save_requested),
            (self.approve_button, self.approve_requested),
        ):
            button.clicked.connect(signal.emit)
            actions.addWidget(button)
        layout.addWidget(self.action_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _place_actions(self) -> None:
        """Schmale Detailansicht: Aktionen in die zweite Zeile, der Kundenname bleibt lesbar."""
        below = self.width() < HEADER_WRAP_WIDTH
        if below == self._actions_below:
            return
        self._actions_below = below
        (self._top_row if below else self._bottom_row).removeWidget(self.action_bar)
        if below:
            self._bottom_row.addWidget(self.action_bar, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self._top_row.addWidget(self.action_bar, 0, Qt.AlignmentFlag.AlignVCenter)

    def _mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.dirty_changed.emit(True)
        self._update_buttons()

    def _label(self, tab: DetailTab) -> str:
        text = SHORT_LABELS.get(tab, tab.value) if self._compact else tab.value
        if tab is DetailTab.VALIDATION and self._open_issues:
            text = f"{text}  {self._open_issues}"
        return text.replace("&", "&&")

    def _relabel(self) -> None:
        metrics = self.tabs.tabBar().fontMetrics()
        needed = (
            sum(metrics.horizontalAdvance(tab.value) + TAB_PADDING + 8 for tab in self.pages) + 24
        )
        self._compact = needed > self.width()
        for tab, page in self.pages.items():
            self.tabs.setTabText(self.tabs.indexOf(page), self._label(tab))

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Kurze Bereichsnamen, wenn die vollen nicht nebeneinander passen."""
        super().resizeEvent(event)
        self._relabel()
        self._place_actions()

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.export:
            self.export.refresh_preview()

    def _update_buttons(self) -> None:
        snapshot = self.snapshot
        status = snapshot.order.status if snapshot else None
        editable = bool(snapshot and snapshot.editable)
        self.save_button.setVisible(editable)
        self.save_button.setEnabled(self.dirty)
        self.ignore_button.setVisible(editable)
        self.approve_button.setVisible(
            status in (OrderStatus.READY, OrderStatus.NEW, OrderStatus.NEEDS_REVIEW)
        )
        ready = status is OrderStatus.READY and not self.dirty
        self.approve_button.setEnabled(ready)
        self.approve_button.setToolTip(
            "Freigeben und Belegnummer vergeben"
            if ready
            else "Erst speichern und alle Pflichtpunkte beheben"
        )

    def show_snapshot(self, snapshot: OrderSnapshot | None, *, keep_tab: bool = True) -> None:
        """Zeigt einen Auftrag; ohne Auftrag den Leerzustand."""
        self.snapshot = snapshot
        self.dirty = False
        self.dirty_changed.emit(False)
        if snapshot is None:
            self.stack.setCurrentIndex(0)
            return
        row_data = snapshot.row
        self.title.setText(row_data.company or "Unbekannter Absender")
        parts = [
            f"Bestellnr. {row_data.order_number}"
            if row_data.order_number
            else "Ohne Bestellnummer",
            f"Eingang {row_data.received_text}",
            plural(row_data.positions, "Position", "Positionen"),
        ]
        if snapshot.order.document_number:
            parts.insert(0, f"Beleg {snapshot.order.document_number}")
        self.subtitle.setText(" · ".join(parts))
        self.chip.set_status(snapshot.order.status)
        for page in self.pages.values():
            page.load(snapshot, self.workbench)  # type: ignore[attr-defined]
        editable_states = (OrderStatus.NEW, OrderStatus.NEEDS_REVIEW, OrderStatus.READY)
        errors = snapshot.validation.errors(snapshot.order.acknowledged)
        self._open_issues = len(errors) if snapshot.order.status in editable_states else 0
        self._relabel()
        if not keep_tab:
            self.tabs.setCurrentIndex(0)
        if self.tabs.currentWidget() is self.export:
            self.export.refresh_preview()
        self.stack.setCurrentIndex(1)
        self._update_buttons()

    def collect(self) -> Order:
        """Auftrag mit allen Eingaben; ``InputError`` bei ungültigen Werten."""
        assert self.snapshot is not None
        order = self.snapshot.order
        for page in (self.overview, self.invoice, self.delivery, self.lines, self.shipping):
            order = page.apply(order)
        return order

    def show_tab(self, tab: DetailTab) -> None:
        """Wechselt in einen Bereich."""
        self.tabs.setCurrentWidget(self.pages[tab])

    def navigate(self, issue: IssueView) -> None:
        """Springt zur Stelle eines Befunds."""
        self.show_tab(issue.tab)
        if issue.tab is DetailTab.LINES:
            self.lines.focus_line(issue.line_index, issue)
        elif issue.tab in (DetailTab.INVOICE, DetailTab.DELIVERY):
            page = self.invoice if issue.tab is DetailTab.INVOICE else self.delivery
            page.focus_problem()
        else:
            self.pages[issue.tab].setFocus()

    def focus_content(self) -> None:
        """Fokus in die Detailansicht (Enter in der Liste)."""
        self.tabs.currentWidget().setFocus()
        if self.tabs.currentWidget() is self.validation:
            self.validation.list.setFocus()
