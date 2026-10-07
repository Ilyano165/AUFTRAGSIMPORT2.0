"""Einstellungen → Postfächer: Server, Verschlüsselung, Anmeldung, Ordner, Verbindungstest.

Passwörter gehen direkt in die Windows-Anmeldeinformationsverwaltung und werden nie
angezeigt; ein leeres Passwortfeld bedeutet „unverändert“. Der Verbindungstest läuft im
Hintergrund, öffnet den Ordner nur lesend und lädt keine Mail.
"""

from __future__ import annotations

import re
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...app.fetching import ProbeResult, probe
from ...app.presentation import Tone
from ...app.workbench import Workbench
from ...config.schema import AuthMethod, MailAccount, TlsMode
from ...domain.errors import AppError
from ...ingest.sources import ImapMailSource, MailSource
from ...services.mail_fetch import SourceFactory, SourceHooks
from ..fetch import BackgroundTask
from ..widgets import Notice, muted, section_label

MAX_PER_RUN = 1000
_SPLIT = re.compile(r"[,;\s]+")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def account_id_for(name: str, taken: set[str]) -> str:
    """Kennung aus dem Namen: Kleinbuchstaben, Ziffern, Bindestrich; eindeutig."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().translate(_UMLAUTS)).strip("-") or "postfach"
    candidate, number = base[:40], 2
    while candidate in taken:
        candidate, number = f"{base[:36]}-{number}", number + 1
    return candidate


class AccountsPage(QWidget):
    """Liste der Postfächer und Bearbeitung des gewählten."""

    accounts_changed = Signal()

    def __init__(
        self,
        workbench: Workbench,
        mail_sources: SourceFactory | None = None,
        *,
        test_mailbox: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workbench = workbench
        self.mail_sources = mail_sources
        self.test_mailbox = test_mailbox
        self.current: MailAccount | None = None
        self.dirty = False
        self._loading = False
        self.probe_task = BackgroundTask(self)
        self.probe_task.finished.connect(self._probe_finished)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 16, 8)
        outer.setSpacing(12)
        outer.addWidget(self._list_column())
        outer.addWidget(self._editor(), 1)
        self.reload()

    def _list_column(self) -> QWidget:
        column = QWidget()
        column.setFixedWidth(290)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("Postfächer"))
        self.list = QListWidget()
        self.list.setAccessibleName("Postfächer")
        self.list.currentRowChanged.connect(self._list_changed)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.new_button = QPushButton("Neu")
        self.new_button.clicked.connect(self.new_account)
        self.delete_button = QPushButton("Löschen")
        self.delete_button.clicked.connect(self.delete_account)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)
        hint = muted(
            "Der Abruf liest nur: Mails werden nicht als gelesen markiert, nicht verschoben "
            "und nicht gelöscht."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return column

    def _editor(self) -> QWidget:
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("DetailTitle")
        layout.addWidget(self.title)
        self.notice = Notice()
        layout.addWidget(self.notice)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit()
        self.host = QLineEdit()
        self.host.setPlaceholderText("z. B. imap.ionos.de")
        self.tls = QComboBox()
        self.tls.addItem("SSL/TLS (Port 993)", TlsMode.IMPLICIT.value)
        self.tls.addItem("STARTTLS (Port 143)", TlsMode.STARTTLS.value)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.username = QLineEdit()
        self.username.setPlaceholderText("meist die Mailadresse")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_state = muted()
        self.folder = QLineEdit()
        self.senders = QLineEdit()
        self.senders.setPlaceholderText("leer = alle; z. B. @kunde.de, einkauf@firma.de")
        self.per_run = QSpinBox()
        self.per_run.setRange(1, MAX_PER_RUN)
        self.enabled = QCheckBox("Postfach aktiv")
        rows: tuple[tuple[str, QWidget], ...] = (
            ("Name", self.name),
            ("Server", self.host),
            ("Verschlüsselung", self.tls),
            ("Port", self.port),
            ("Benutzername", self.username),
            ("Passwort", self.password),
            ("", self.password_state),
            ("Ordner", self.folder),
            ("Erlaubte Absender", self.senders),
            ("Höchstens je Abruf", self.per_run),
            ("", self.enabled),
        )
        for label, widget in rows:
            form.addRow(label, widget)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.test_button = QPushButton("Verbindung testen")
        self.test_button.clicked.connect(self.test_connection)
        self.save_button = QPushButton("Speichern")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        for line in (self.name, self.host, self.username, self.password, self.folder, self.senders):
            line.textEdited.connect(self._edited)
        for spin in (self.port, self.per_run):
            spin.valueChanged.connect(self._edited)
        self.enabled.toggled.connect(self._edited)
        self.tls.currentIndexChanged.connect(self._tls_changed)
        return editor

    # ---------- Laden ----------

    def reload(self, select: str | None = None) -> None:
        """Liste neu aufbauen und ein Postfach anzeigen."""
        wanted = select or (self.current.id if self.current else None)
        self.list.blockSignals(True)
        self.list.clear()
        for account in self.workbench.accounts:
            item = QListWidgetItem(account.name if account.enabled else f"{account.name} (inaktiv)")
            item.setData(Qt.ItemDataRole.UserRole, account.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = [a.id for a in self.workbench.accounts]
        if ids:
            self.list.setCurrentRow(ids.index(wanted) if wanted in ids else 0)
            self._show(self.workbench.accounts[self.list.currentRow()])
        else:
            self._show(None)

    def _show(self, account: MailAccount | None) -> None:
        self._loading = True
        self.current = account
        shown = account or MailAccount(id="", name="")
        self.title.setText(account.name if account else "Noch kein Postfach eingerichtet")
        self.name.setText(shown.name)
        self.host.setText(shown.host)
        self.tls.setCurrentIndex(0 if shown.tls_mode is TlsMode.IMPLICIT else 1)
        self.port.setValue(shown.port)
        self.username.setText(shown.username)
        self.password.clear()
        self.folder.setText(shown.folder)
        self.senders.setText(", ".join(shown.allowed_senders))
        self.per_run.setValue(min(shown.max_messages_per_run, MAX_PER_RUN))
        self.enabled.setChecked(shown.enabled)
        self.password_state.setText(self._password_text(account))
        editable: tuple[QWidget, ...] = (
            self.name,
            self.host,
            self.tls,
            self.port,
            self.username,
            self.password,
            self.folder,
            self.senders,
            self.per_run,
            self.enabled,
            self.save_button,
            self.test_button,
            self.delete_button,
        )
        for widget in editable:
            widget.setEnabled(account is not None)
        if account is not None and account.auth_method is AuthMethod.OAUTH2:
            self.notice.show_text(
                Tone.WARNING,
                "Dieses Postfach nutzt OAuth2 (etwa Microsoft 365). Das wird noch nicht "
                "unterstützt; der Abruf ist für dieses Postfach nicht möglich.",
            )
            self.test_button.setEnabled(False)
        else:
            self.notice.hide()
        self.dirty = False
        self._loading = False

    def _password_text(self, account: MailAccount | None) -> str:
        if account is None or not account.id:
            return "Noch kein Passwort gespeichert"
        try:
            stored = self.workbench.has_password(account.id)
        except AppError:
            return "Windows-Anmeldeinformationsverwaltung nicht verfügbar"
        if stored:
            return "Passwort ist gespeichert (Feld leer lassen = unverändert)"
        return "Noch kein Passwort gespeichert"

    # ---------- Bearbeiten ----------

    def _edited(self, *_args: object) -> None:
        if not self._loading:
            self.dirty = True

    def _tls_changed(self, _index: int) -> None:
        if self._loading:
            return
        implicit = self.tls.currentData() == TlsMode.IMPLICIT.value
        if implicit and self.port.value() == 143:
            self.port.setValue(993)
        elif not implicit and self.port.value() == 993:
            self.port.setValue(143)
        self._edited()

    def edited_account(self) -> MailAccount:
        """Postfach aus den Eingaben (noch nicht gespeichert)."""
        base = self.current or MailAccount(id="", name="")
        taken = {a.id for a in self.workbench.accounts if a.id != base.id}
        name = self.name.text().strip()
        senders = tuple(s for s in _SPLIT.split(self.senders.text().strip().lower()) if s)
        return replace(
            base,
            id=base.id or account_id_for(name, taken),
            name=name,
            host=self.host.text().strip(),
            port=self.port.value(),
            tls_mode=TlsMode(self.tls.currentData()),
            username=self.username.text().strip(),
            folder=self.folder.text().strip() or "INBOX",
            allowed_senders=senders,
            max_messages_per_run=self.per_run.value(),
            enabled=self.enabled.isChecked(),
        )

    def new_account(self) -> None:
        """Neues Postfach mit sicheren Standardwerten."""
        if not self.confirm_leave():
            return
        self._show(MailAccount(id="", name="Neues Postfach"))
        self.title.setText("Neues Postfach")
        self.list.clearSelection()
        self.dirty = True

    def save(self) -> bool:
        """Speichert Postfach und (falls eingegeben) Passwort."""
        account = self.edited_account()
        try:
            self.workbench.save_account(account)
            if self.password.text():
                self.workbench.set_password(account.id, self.password.text())
        except AppError as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return False
        self.password.clear()
        self.dirty = False
        self.reload(select=account.id)
        self.notice.show_text(Tone.SUCCESS, f"Postfach „{account.name}“ gespeichert.")
        self.accounts_changed.emit()
        return True

    def delete_account(self) -> None:
        """Löscht das Postfach samt gespeichertem Passwort."""
        if self.current is None:
            return
        if not self.current.id:
            self.dirty = False
            self.reload()
            return
        answer = QMessageBox.question(
            self,
            "Postfach löschen",
            f"Postfach „{self.current.name}“ und sein gespeichertes Passwort löschen? "
            "Bereits abgerufene Aufträge bleiben erhalten.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.workbench.delete_account(self.current.id)
        except (ValueError, AppError) as exc:
            self.notice.show_text(Tone.DANGER, str(exc))
            return
        self.current = None
        self.reload()
        self.accounts_changed.emit()

    def confirm_leave(self) -> bool:
        """Ungespeicherte Änderungen abfragen."""
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Die Änderungen am Postfach wurden noch nicht gespeichert.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self.dirty = False
            return True
        return False

    def _list_changed(self, row: int) -> None:
        if row < 0 or self._loading:
            return
        accounts = self.workbench.accounts
        if row >= len(accounts) or (self.current and accounts[row].id == self.current.id):
            return
        if not self.confirm_leave():
            self.list.blockSignals(True)
            ids = [a.id for a in accounts]
            self.list.setCurrentRow(ids.index(self.current.id) if self.current in accounts else -1)
            self.list.blockSignals(False)
            return
        self._show(accounts[row])

    # ---------- Verbindungstest ----------

    def test_connection(self) -> None:
        """Anmelden und Ordner lesend öffnen, im Hintergrund; lädt keine Mail."""
        if self.probe_task.running:
            return
        account = self.edited_account()
        typed = self.password.text()
        sources = self.mail_sources

        def source(hooks: SourceHooks) -> MailSource:
            if typed and not self.test_mailbox:
                return ImapMailSource(account, lambda: typed, sleep=hooks.sleep, max_attempts=1)
            if sources is None:
                raise RuntimeError("Keine Mailquelle verfügbar")
            return sources(account, hooks)

        self.test_button.setEnabled(False)
        self.notice.show_text(Tone.INFO, "Verbindung wird geprüft …")
        self.probe_task.start(lambda _emit: probe(account, source))

    def _probe_finished(self, result: object) -> None:
        self.test_button.setEnabled(self.current is not None)
        if isinstance(result, ProbeResult) and result.failure is None:
            self.notice.show_text(
                Tone.SUCCESS,
                f"Verbindung erfolgreich: {result.messages} Nachrichten im Ordner "
                f"„{result.folder}“. Es wurde nichts verändert.",
            )
        elif isinstance(result, ProbeResult) and result.failure is not None:
            self.notice.show_text(Tone.DANGER, f"{result.failure.what}. {result.failure.action}")
        else:
            self.notice.show_text(Tone.DANGER, "Der Verbindungstest ist fehlgeschlagen.")
