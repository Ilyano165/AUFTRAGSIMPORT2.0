"""Einstellungen → Firmenprofile."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...app.presentation import Tone, plural
from ...app.workbench import Workbench
from ...config.schema import (
    DEFAULT_TAX_RATES,
    EXPORT_ENCODINGS,
    TEMPLATE_PLACEHOLDERS,
    ImportProfile,
    MailTemplate,
    PriceMode,
    SenderAction,
    SenderRule,
    ShippingOption,
)
from ...domain.errors import AppError
from ...domain.models import Address, Contact, Order, OrderLine, PaymentMethod
from ...domain.provenance import Field
from ...services.profile_rules import evaluate_sender, render_template, template_values
from ..widgets import Notice, muted, row, section_label

ADDRESS_FIELDS = (
    ("company", "Firmenname"),
    ("street", "Straße"),
    ("house_number", "Nr."),
    ("postal_code", "PLZ"),
    ("city", "Ort"),
    ("country", "Land"),
    ("phone", "Telefon"),
    ("email", "E-Mail"),
)
_SLUG = re.compile(r"[^a-z0-9]+")


class InputProblem(ValueError):
    """Eingabe im Profil ist ungültig."""


def slug(text: str, taken: set[str]) -> str:
    """Profil-ID aus dem Namen, eindeutig."""
    base = _SLUG.sub(
        "-",
        text.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"),
    )
    base = base.strip("-")[:28] or "profil"
    candidate, counter = base, 2
    while candidate in taken:
        candidate, counter = f"{base}-{counter}", counter + 1
    return candidate


def _decimal(text: str, label: str) -> Decimal | None:
    cleaned = text.strip().replace("€", "").replace("%", "").replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise InputProblem(f"{label}: „{text}“ ist keine Zahl") from exc


def _sample_order(profile: ImportProfile) -> Order:
    return Order(
        id="beispiel",
        profile_id=profile.id,
        document_number=f"{profile.document_prefix}-{date.today().year}-000123",
        customer_reference=Field.manual("B-4711"),
        invoice_address=Field.manual(Address(company="Beispielkunde GmbH")),
        contact=Field.manual(Contact("", "Erika", "Muster")),
        order_date=Field.manual(date.today()),
        lines=(OrderLine(1, "Beispielartikel", Field.manual(Decimal(3))),),
    )


class ProfilesPage(QWidget):
    """Liste der Profile und Bearbeitung des gewählten Profils."""

    profiles_changed = Signal()

    def __init__(self, workbench: Workbench, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.original: ImportProfile | None = None
        self.is_new = False
        self.dirty = False
        self._loading = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 16, 8)
        outer.setSpacing(12)
        outer.addWidget(self._list_column())
        outer.addWidget(self._editor(), 1)
        self.reload(select=workbench.profile.id)

    # ---------- Aufbau ----------
    def _list_column(self) -> QWidget:
        column = QWidget()
        column.setFixedWidth(290)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("Firmenprofile"))
        self.list = QListWidget()
        self.list.setAccessibleName("Firmenprofile")
        self.list.currentRowChanged.connect(self._list_changed)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.new_button = QPushButton("Neu")
        self.copy_button = QPushButton("Duplizieren")
        self.delete_button = QPushButton("Löschen")
        for button, handler in (
            (self.new_button, self.new_profile),
            (self.copy_button, self.duplicate_profile),
            (self.delete_button, self.delete_profile),
        ):
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(
            muted("Jedes Profil hat eigene Aufträge, Kataloge, Nummernkreise und Exportordner.")
        )
        layout.itemAt(layout.count() - 1).widget().setWordWrap(True)  # type: ignore[union-attr]
        return column

    def _editor(self) -> QWidget:
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("DetailTitle")
        self.info = muted()
        layout.addWidget(self.title)
        layout.addWidget(self.info)
        self.notice = Notice()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("Detail")
        self.tabs.addTab(self._scroll(self._company_tab()), "Firma")
        self.tabs.addTab(self._scroll(self._mail_tab()), "Postfach && Absender")
        self.tabs.addTab(self._scroll(self._export_tab()), "Lexware && Export")
        self.tabs.addTab(self._scroll(self._shipping_tab()), "Versand && Zahlung")
        self.tabs.addTab(self._scroll(self._template_tab()), "Mailvorlage")
        layout.addWidget(self.tabs, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.discard_button = QPushButton("Verwerfen")
        self.discard_button.clicked.connect(
            lambda: self.reload(
                select=self.original.id if self.original and not self.is_new else None
            )
        )
        self.save_button = QPushButton("Speichern")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.discard_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        return editor

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(16, 14, 16, 16)
        widget.setMaximumWidth(760)
        layout.addWidget(widget)
        layout.addStretch(1)
        area.setWidget(holder)
        return area

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        return form

    def _line(self, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.textEdited.connect(self._changed)
        return edit

    def _company_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = self._form()
        self.name = self._line("z. B. Yellotools")
        self.profile_id = self._line("wird aus dem Namen gebildet")
        self.profile_id.setMaxLength(32)
        self.address = {key: self._line() for key, _ in ADDRESS_FIELDS}
        self.address["house_number"].setMaximumWidth(90)
        self.address["postal_code"].setMaximumWidth(90)
        self.address["country"].setMaximumWidth(60)
        self.address["country"].setMaxLength(2)
        form.addRow("Profilname", self.name)
        form.addRow("Profil-ID", self.profile_id)
        layout.addLayout(form)
        layout.addWidget(section_label("Lieferantenadresse (eigene Firma)"))
        form2 = self._form()
        a = self.address
        form2.addRow("Firmenname", a["company"])
        form2.addRow("Straße, Nr.", row(a["street"], a["house_number"], stretch=(1, 0)))
        form2.addRow("PLZ, Ort", row(a["postal_code"], a["city"], stretch=(0, 1)))
        form2.addRow("Land", a["country"])
        form2.addRow("Telefon", a["phone"])
        form2.addRow("E-Mail", a["email"])
        layout.addLayout(form2)
        return page

    def _mail_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = self._form()
        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self._changed)
        self.sender_default = QComboBox()
        for action in SenderAction:
            self.sender_default.addItem(action.label, action.value)
        self.sender_default.currentIndexChanged.connect(self._changed)
        form.addRow("Mailkonto", self.account)
        form.addRow("Unbekannte Absender", self.sender_default)
        layout.addLayout(form)
        layout.addWidget(section_label("Absenderregeln"))
        layout.addWidget(
            muted(
                "Genaue Adresse (einkauf@kunde.de) vor Domain (@kunde.de); Domains gelten "
                "auch für Subdomains."
            )
        )
        self.rules = QTableWidget(0, 2)
        self.rules.setHorizontalHeaderLabels(["Absender", "Aktion"])
        self.rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rules.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        action_width = self.fontMetrics().horizontalAdvance(SenderAction.REVIEW.label) + 60
        self.rules.horizontalHeader().resizeSection(1, action_width)
        self.rules.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.rules.verticalHeader().hide()
        self.rules.setMinimumHeight(150)
        self.rules.itemChanged.connect(self._changed)
        layout.addWidget(self.rules)
        buttons = QHBoxLayout()
        add, remove = QPushButton("Regel hinzufügen"), QPushButton("Regel entfernen")
        add.clicked.connect(lambda: self._add_rule(SenderRule("", SenderAction.ACCEPT), focus=True))
        remove.clicked.connect(lambda: self._remove_row(self.rules))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(section_label("Regel testen"))
        self.sender_test = QLineEdit()
        self.sender_test.setPlaceholderText("absender@kunde.de")
        self.sender_test.textChanged.connect(self._test_sender)
        self.sender_test.setMaximumWidth(360)
        self.sender_result = muted()
        self.sender_result.setWordWrap(True)
        layout.addWidget(self.sender_test)
        layout.addWidget(self.sender_result)
        return page

    def _export_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = self._form()
        self.export_dir = self._line(r"z. B. \\server\lexware\import")
        self.test_dir = self._line("Ordner für Testexporte")
        self.network = QCheckBox("Netzlaufwerk als Exportziel erlauben")
        self.network.toggled.connect(self._changed)
        self.prefix = self._line("AU")
        self.prefix.setMaxLength(8)
        self.prefix.setMaximumWidth(120)
        self.price_mode = QComboBox()
        self.price_mode.addItem("Nettopreise", PriceMode.NET.value)
        self.price_mode.addItem("Bruttopreise", PriceMode.GROSS.value)
        self.price_mode.currentIndexChanged.connect(self._changed)
        self.encoding = QComboBox()
        self.encoding.addItems(list(EXPORT_ENCODINGS))
        self.encoding.currentIndexChanged.connect(self._changed)
        self.tax_rates = self._line("0; 7; 19")
        self.tax_rates.setMaximumWidth(200)
        self.target = muted()
        self.target.setWordWrap(True)
        form.addRow(
            "Lexware-Importpfad",
            row(self.export_dir, self._browse(self.export_dir), stretch=(1, 0)),
        )
        form.addRow("Testordner", row(self.test_dir, self._browse(self.test_dir), stretch=(1, 0)))
        form.addRow("", self.network)
        form.addRow("Belegpräfix", self.prefix)
        form.addRow("Preisangaben", self.price_mode)
        form.addRow("Zeichensatz", self.encoding)
        form.addRow("Steuersätze (%)", self.tax_rates)
        form.addRow("Zielsystem", self.target)
        layout.addWidget(section_label("Exportregeln"))
        layout.addLayout(form)
        return page

    def _browse(self, target: QLineEdit) -> QPushButton:
        button = QPushButton("Auswählen …")
        button.clicked.connect(lambda: self._choose_folder(target))
        return button

    def _choose_folder(self, target: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ordner auswählen", target.text())
        if folder:
            target.setText(folder)
            self._changed()

    def _shipping_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("Versandmethoden"))
        layout.addWidget(muted("Erkennungsbegriffe durch Semikolon trennen, z. B. „DHL; Paket“."))
        self.shipping = QTableWidget(0, 3)
        self.shipping.setHorizontalHeaderLabels(
            ["Bezeichnung", "Erkennungsbegriffe", "Versandkosten (€)"]
        )
        header = self.shipping.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.shipping.verticalHeader().hide()
        self.shipping.setMinimumHeight(140)
        self.shipping.itemChanged.connect(self._changed)
        layout.addWidget(self.shipping)
        buttons = QHBoxLayout()
        add, remove = QPushButton("Versandmethode hinzufügen"), QPushButton("Entfernen")
        add.clicked.connect(lambda: self._add_shipping(ShippingOption(""), focus=True))
        remove.clicked.connect(lambda: self._remove_row(self.shipping))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(section_label("Zahlungsmethoden"))
        grid = QGridLayout()
        self.payments: dict[PaymentMethod, QCheckBox] = {}
        for index, method in enumerate(PaymentMethod):
            box = QCheckBox(method.label)
            box.toggled.connect(self._changed)
            self.payments[method] = box
            grid.addWidget(box, index // 3, index % 3)
        layout.addLayout(grid)
        form = self._form()
        self.default_payment = QComboBox()
        self.default_payment.addItem("Keine (aus der Mail)", "")
        for method in PaymentMethod:
            self.default_payment.addItem(method.label, method.value)
        self.default_payment.currentIndexChanged.connect(self._changed)
        form.addRow("Standard-Zahlungsart", self.default_payment)
        layout.addLayout(form)
        return page

    def _template_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("Auftragsbestätigung an den Kunden"))
        form = self._form()
        self.subject = self._line()
        self.subject.textChanged.connect(self._preview_template)
        self.body = QPlainTextEdit()
        self.body.setMinimumHeight(150)
        self.body.setTabChangesFocus(True)
        self.body.textChanged.connect(self._changed)
        self.body.textChanged.connect(self._preview_template)
        form.addRow("Betreff", self.subject)
        form.addRow("Text", self.body)
        layout.addLayout(form)
        listed = " · ".join(f"{{{key}}} {text}" for key, text in TEMPLATE_PLACEHOLDERS.items())
        hint = muted(f"Platzhalter: {listed}")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(section_label("Vorschau"))
        self.template_preview = QPlainTextEdit()
        self.template_preview.setReadOnly(True)
        self.template_preview.setMinimumHeight(150)
        self.template_preview.setTabChangesFocus(True)
        layout.addWidget(self.template_preview)
        return page

    # ---------- Tabellenzeilen ----------
    def _add_rule(self, rule: SenderRule, *, focus: bool = False) -> None:
        index = self.rules.rowCount()
        self.rules.insertRow(index)
        self.rules.setItem(index, 0, QTableWidgetItem(rule.pattern))
        combo = QComboBox()
        for action in SenderAction:
            combo.addItem(action.label, action.value)
        combo.setCurrentIndex(combo.findData(rule.action.value))
        combo.currentIndexChanged.connect(self._changed)
        self.rules.setCellWidget(index, 1, combo)
        if focus:
            self.rules.setCurrentCell(index, 0)
            cell = self.rules.item(index, 0)
            if cell is not None:
                self.rules.editItem(cell)
            self._changed()

    def _add_shipping(self, option: ShippingOption, *, focus: bool = False) -> None:
        index = self.shipping.rowCount()
        self.shipping.insertRow(index)
        fee = "" if option.fee is None else f"{option.fee:.2f}".replace(".", ",")
        for column, text in enumerate((option.name, "; ".join(option.aliases), fee)):
            self.shipping.setItem(index, column, QTableWidgetItem(text))
        if focus:
            self.shipping.setCurrentCell(index, 0)
            cell = self.shipping.item(index, 0)
            if cell is not None:
                self.shipping.editItem(cell)
            self._changed()

    def _remove_row(self, table: QTableWidget) -> None:
        if table.currentRow() >= 0:
            table.removeRow(table.currentRow())
            self._changed()

    # ---------- Laden und Sammeln ----------
    def reload(self, select: str | None = None) -> None:
        """Liste neu aufbauen und ein Profil anzeigen."""
        self.list.blockSignals(True)
        self.list.clear()
        for profile in self.workbench.profiles:
            item = QListWidgetItem(profile.name)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            item.setToolTip(profile.company_name)
            self.list.addItem(item)
        ids = [p.id for p in self.workbench.profiles]
        wanted = select if select in ids else self.workbench.profile.id
        self.list.setCurrentRow(ids.index(wanted))
        self.list.blockSignals(False)
        self._show(self.workbench.settings.profile(wanted), new=False)

    def _list_changed(self, row_number: int) -> None:
        if row_number < 0:
            return
        profile_id = self.list.item(row_number).data(Qt.ItemDataRole.UserRole)
        if self.original is not None and profile_id == self.original.id and not self.is_new:
            return
        if not self.confirm_leave():
            self.list.blockSignals(True)
            current = [
                self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
            ]
            if self.original and self.original.id in current:
                self.list.setCurrentRow(current.index(self.original.id))
            self.list.blockSignals(False)
            return
        self._show(self.workbench.settings.profile(profile_id), new=False)

    def _show(self, profile: ImportProfile, *, new: bool) -> None:
        self._loading = True
        self.original, self.is_new = profile, new
        self.title.setText(profile.name or "Neues Profil")
        if new:
            self.info.setText("Neues Profil – noch nicht gespeichert")
        else:
            version, _ = self.workbench.catalogs.active(profile.id)
            catalog = (
                f"Katalog Version {version.number}, {version.article_count} Artikel"
                if version
                else "noch kein Katalog"
            )
            orders = plural(self.workbench.order_count(profile.id), "Auftrag", "Aufträge")
            active = " · aktives Profil" if profile.id == self.workbench.profile.id else ""
            self.info.setText(f"{orders} · {catalog}{active}")
        self.notice.hide()
        self.name.setText(profile.name)
        self.profile_id.setText(profile.id)
        self.profile_id.setReadOnly(not new)
        for key, edit in self.address.items():
            edit.setText(getattr(profile.supplier, key))
        self.account.clear()
        self.account.addItem("Kein Postfach zugeordnet", "")
        for account in self.workbench.settings.accounts:
            self.account.addItem(f"{account.name} ({account.username or account.id})", account.id)
        self.account.setCurrentIndex(max(0, self.account.findData(profile.mail_account_id)))
        self.sender_default.setCurrentIndex(
            self.sender_default.findData(profile.sender_default.value)
        )
        self.rules.setRowCount(0)
        for rule in profile.sender_rules:
            self._add_rule(rule)
        self.export_dir.setText(profile.export_dir)
        self.test_dir.setText(profile.test_export_dir)
        self.network.setChecked(profile.export_dir_network_allowed)
        self.prefix.setText(profile.document_prefix)
        self.price_mode.setCurrentIndex(self.price_mode.findData(profile.price_mode.value))
        self.encoding.setCurrentText(profile.export_encoding)
        self.tax_rates.setText("; ".join(f"{r:g}" for r in profile.tax_rates))
        self.target.setText(
            f"{profile.target_system}, validiert am {profile.target_validated_on}"
            if profile.target_validated
            else "Noch nicht validiert – Produktivexport gesperrt, Testexporte möglich"
        )
        self.shipping.setRowCount(0)
        for option in profile.shipping_methods:
            self._add_shipping(option)
        for method, box in self.payments.items():
            box.setChecked(method in profile.payment_methods)
        default = profile.default_payment_method.value if profile.default_payment_method else ""
        self.default_payment.setCurrentIndex(max(0, self.default_payment.findData(default)))
        self.subject.setText(profile.mail_template.subject)
        self.body.setPlainText(profile.mail_template.body)
        self.delete_button.setEnabled(not new and profile.id != self.workbench.profile.id)
        self.copy_button.setEnabled(not new)
        self._loading = False
        self._set_dirty(new)
        self._test_sender()
        self._preview_template()

    def _changed(self, *_args: object) -> None:
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.save_button.setEnabled(dirty)
        self.discard_button.setEnabled(dirty)

    def collect(self) -> ImportProfile:
        """Profil aus dem Formular; ``InputProblem`` bei unlesbaren Zahlen."""
        assert self.original is not None
        rules = []
        for index in range(self.rules.rowCount()):
            item = self.rules.item(index, 0)
            pattern = item.text().strip().lower() if item else ""
            combo = self.rules.cellWidget(index, 1)
            if pattern and isinstance(combo, QComboBox):
                rules.append(SenderRule(pattern, SenderAction(combo.currentData())))
        options = []
        for index in range(self.shipping.rowCount()):
            cells = [self.shipping.item(index, c) for c in range(3)]
            name = cells[0].text().strip() if cells[0] else ""
            if not name:
                continue
            aliases = tuple(
                a.strip() for a in (cells[1].text() if cells[1] else "").split(";") if a.strip()
            )
            options.append(
                ShippingOption(
                    name,
                    aliases,
                    _decimal(cells[2].text() if cells[2] else "", f"Versandkosten {name}"),
                )
            )
        rates = tuple(
            r
            for r in (
                _decimal(t, "Steuersatz")
                for t in re.split(r"[;,]", self.tax_rates.text())
                if t.strip()
            )
            if r is not None
        )
        default = self.default_payment.currentData()
        supplier = replace(
            self.original.supplier, **{k: e.text().strip() for k, e in self.address.items()}
        )
        return replace(
            self.original,
            id=self.profile_id.text().strip() if self.is_new else self.original.id,
            name=self.name.text().strip(),
            supplier=replace(supplier, country=supplier.country.upper()),
            mail_account_id=str(self.account.currentData() or ""),
            sender_rules=tuple(rules),
            sender_default=SenderAction(self.sender_default.currentData()),
            export_dir=self.export_dir.text().strip(),
            test_export_dir=self.test_dir.text().strip(),
            export_dir_network_allowed=self.network.isChecked(),
            document_prefix=self.prefix.text().strip().upper(),
            price_mode=PriceMode(self.price_mode.currentData()),
            export_encoding=self.encoding.currentText(),
            tax_rates=tuple(sorted(set(rates))) or DEFAULT_TAX_RATES,
            shipping_methods=tuple(options),
            payment_methods=tuple(m for m, box in self.payments.items() if box.isChecked()),
            default_payment_method=PaymentMethod(default) if default else None,
            mail_template=MailTemplate(self.subject.text(), self.body.toPlainText()),
        )

    def save(self) -> bool:
        """Prüft alle Profile gemeinsam und speichert."""
        try:
            profile = self.collect()
            if not profile.payment_methods:
                raise InputProblem("Mindestens eine Zahlungsmethode muss freigegeben sein")
            if self.is_new and profile.id in {p.id for p in self.workbench.profiles}:
                raise InputProblem(f"Die Profil-ID „{profile.id}“ ist bereits vergeben")
            self.workbench.save_profile(profile)
        except InputProblem as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return False
        except AppError as exc:
            details = getattr(exc, "details", ()) or (str(exc),)
            self.notice.show_text(
                Tone.DANGER, "Nicht gespeichert:\n" + "\n".join(f"• {d}" for d in details)
            )
            return False
        self._set_dirty(False)
        self.reload(select=profile.id)
        self.notice.show_text(Tone.SUCCESS, "Gespeichert.")
        self.profiles_changed.emit()
        return True

    def confirm_leave(self) -> bool:
        """Fragt bei ungespeicherten Änderungen."""
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Die Änderungen am Profil wurden noch nicht gespeichert.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self._set_dirty(False)
            return True
        return False

    # ---------- Aktionen ----------
    def new_profile(self) -> None:
        """Neues, leeres Profil (wird erst beim Speichern angelegt)."""
        if not self.confirm_leave():
            return
        taken = {p.id for p in self.workbench.profiles}
        self._show(
            ImportProfile(
                id=slug("neues-profil", taken), name="Neues Profil", document_prefix="AU"
            ),
            new=True,
        )
        self.list.clearSelection()
        self.tabs.setCurrentIndex(0)
        self.name.setFocus()
        self.name.selectAll()

    def duplicate_profile(self) -> None:
        """Kopie ohne Exportordner und Postfach (beides muss je Firma eindeutig sein)."""
        if self.original is None or not self.confirm_leave():
            return
        taken = {p.id for p in self.workbench.profiles}
        copy = replace(
            self.original,
            id=slug(f"{self.original.id}-kopie", taken),
            name=f"{self.original.name} (Kopie)",
            export_dir="",
            test_export_dir="",
            mail_account_id="",
            target_system="",
            target_validated_on="",
        )
        self._show(copy, new=True)
        self.notice.show_text(
            Tone.INFO, "Kopie: Exportordner, Testordner und Postfach bitte neu festlegen."
        )

    def delete_profile(self) -> None:
        """Löscht ein Profil ohne Aufträge."""
        if self.original is None or self.is_new:
            return
        answer = QMessageBox.question(
            self, "Profil löschen", f"Profil „{self.original.name}“ löschen?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.workbench.delete_profile(self.original.id)
        except (ValueError, AppError) as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return
        self._set_dirty(False)
        self.reload()
        self.profiles_changed.emit()

    def _test_sender(self) -> None:
        address = self.sender_test.text().strip()
        if not address or "@" not in address or self.original is None:
            self.sender_result.setText("Adresse eingeben, um die Regeln zu prüfen")
            return
        try:
            decision = evaluate_sender(self.collect(), address)
        except InputProblem:
            return
        self.sender_result.setText(f"→ {decision.action.label} ({decision.explanation})")

    def _preview_template(self) -> None:
        if self._loading or self.original is None:
            return
        try:
            profile = self.collect()
        except InputProblem:
            return
        subject, body = render_template(
            profile.mail_template, template_values(_sample_order(profile), profile)
        )
        self.template_preview.setPlainText(f"Betreff: {subject}\n\n{body}")
