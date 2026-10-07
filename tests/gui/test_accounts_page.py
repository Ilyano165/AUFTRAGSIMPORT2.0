"""Einstellungen → Postfächer und der vollständige Ablauf über einen echten IMAP-Server."""

from __future__ import annotations

import functools
import hashlib
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

from icware_auftragsimport.app import fetching
from icware_auftragsimport.app.demo import build_demo
from icware_auftragsimport.app.fetching import folder_sources, imap_sources
from icware_auftragsimport.app.testmails import TEST_MAILBOX
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.config.schema import TlsMode, settings_to_dict
from icware_auftragsimport.gui import fetch as fetch_ui
from icware_auftragsimport.gui.main_window import MainWindow
from icware_auftragsimport.gui.settings.accounts import AccountsPage, account_id_for
from icware_auftragsimport.gui.settings.dialog import SettingsDialog
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.ingest.sources import ImapMailSource
from icware_auftragsimport.security.credentials import MemoryCredentialStore, credential_key
from icware_auftragsimport.services.mail_fetch import FetchSummary
from imapserver import PASSWORD, DovecotServer, unavailable_reason
from mailcorpus import corpus

from .conftest import NOW

SECRET = "S3hr-Geheim!Passwort"
REASON = unavailable_reason()
needs_dovecot = pytest.mark.skipif(bool(REASON), reason=f"NOT TESTED – {REASON}")


@pytest.fixture
def store() -> MemoryCredentialStore:
    return MemoryCredentialStore()


@pytest.fixture
def bench(tmp_path: Path, store: MemoryCredentialStore) -> Workbench:
    database, settings = build_demo(tmp_path / "demo", NOW)
    return Workbench(database.connect(), settings, FixedClock(NOW), credentials=store)


def wait_until(qapp: QApplication, condition: Callable[[], bool], seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("Zeitüberschreitung beim Warten auf die Oberfläche")
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


def fill(page: AccountsPage, **values: object) -> None:
    for name, value in values.items():
        widget = getattr(page, name)
        if isinstance(widget, QLineEdit):
            widget.setText(str(value))
            widget.textEdited.emit(str(value))
        elif name == "tls":
            widget.setCurrentIndex(0 if value is TlsMode.IMPLICIT else 1)
        else:
            widget.setValue(value)


def visible_texts(page: AccountsPage) -> str:
    texts = [w.text() for w in page.findChildren(QLineEdit)] + [
        w.text() for w in page.findChildren(QLabel)
    ]
    return "\n".join(texts)


def probe_and_wait(qapp: QApplication, page: AccountsPage) -> str:
    page.test_connection()
    wait_until(qapp, lambda: not page.probe_task.running)
    return page.notice.text()


# ---------- Anlegen, Bearbeiten, Löschen, Passwort ----------


def test_create_account_with_password_never_shows_or_stores_it_in_settings(
    qapp: QApplication, bench: Workbench, store: MemoryCredentialStore
) -> None:
    page = AccountsPage(bench)
    before = len(bench.accounts)
    page.new_account()
    fill(
        page,
        name="Einkauf Nord",
        host="imap.ionos.de",
        username="einkauf@kunde.de",
        password=SECRET,
    )
    assert page.password.echoMode() is QLineEdit.EchoMode.Password
    assert page.save()

    account = bench.accounts[-1]
    assert len(bench.accounts) == before + 1 and account.id == "einkauf-nord"
    assert (account.host, account.port, account.folder) == ("imap.ionos.de", 993, "INBOX")
    assert store.get(credential_key("einkauf-nord")) == SECRET
    assert page.password.text() == "" and SECRET not in visible_texts(page)
    assert "Passwort ist gespeichert" in page.password_state.text()
    assert SECRET not in json.dumps(settings_to_dict(bench.settings))
    page.reload(select="einkauf-nord")
    assert page.password.text() == "" and SECRET not in visible_texts(page)


def test_edit_keeps_password_when_field_is_left_empty(
    qapp: QApplication, bench: Workbench, store: MemoryCredentialStore
) -> None:
    page = AccountsPage(bench)
    bench.set_password("bestellungen", SECRET)
    page.reload(select="bestellungen")
    fill(page, folder="Bestellungen", tls=TlsMode.STARTTLS)
    assert page.port.value() == 143, "Port folgt der Verschlüsselung"
    assert page.save()
    assert bench.settings.account("bestellungen").folder == "Bestellungen"
    assert bench.settings.account("bestellungen").tls_mode is TlsMode.STARTTLS
    assert store.get(credential_key("bestellungen")) == SECRET


def test_invalid_input_is_rejected_with_message(qapp: QApplication, bench: Workbench) -> None:
    page = AccountsPage(bench)
    page.new_account()
    fill(page, name="Kaputt", host="imap.example.de", username="u", senders="kein-absender")
    assert not page.save()
    assert "keine Mailadresse oder Domain" in page.notice.text()


def test_delete_unused_account_removes_password(
    qapp: QApplication,
    bench: Workbench,
    store: MemoryCredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    page = AccountsPage(bench)
    page.new_account()
    fill(page, name="Wegwerf", host="imap.example.de", username="w@example.de", password=SECRET)
    page.save()
    page.delete_account()
    assert "wegwerf" not in {a.id for a in bench.accounts}
    assert store.get(credential_key("wegwerf")) is None


def test_account_in_use_cannot_be_deleted(
    qapp: QApplication, bench: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    page = AccountsPage(bench)
    page.reload(select="bestellungen")
    page.delete_account()
    assert "bestellungen" in {a.id for a in bench.accounts}
    assert "wird von" in page.notice.text()


def test_multiple_accounts_and_choosing_the_active_one(
    qapp: QApplication, bench: Workbench
) -> None:
    dialog = SettingsDialog(bench, mail_sources=folder_sources(Path("/x")))
    page = dialog.accounts
    page.new_account()
    fill(page, name="Zweitpostfach", host="imap.strato.de", username="z@kunde.de")
    page.save()
    names = [page.list.item(i).text() for i in range(page.list.count())]
    assert "Zweitpostfach" in names and len(names) >= 3
    page.list.setCurrentRow(0)
    first = bench.accounts[0]
    assert page.host.text() == first.host

    profiles = dialog.profiles
    profiles.reload(select=bench.profile.id)
    index = profiles.account.findData("zweitpostfach")
    assert index >= 0, "neues Postfach steht im Profil zur Auswahl"
    profiles.account.setCurrentIndex(index)
    bench.save_profile(replace(bench.profile, mail_account_id="zweitpostfach"))
    job = bench.fetch_job(folder_sources(Path("/x")))
    assert not isinstance(job, fetch_ui.FetchFailure)
    assert job.targets[0].account.id == "zweitpostfach"


def test_account_ids_are_stable_slugs() -> None:
    assert account_id_for("Bestellungen Köln", set()) == "bestellungen-koeln"
    assert account_id_for("Bestellungen Köln", {"bestellungen-koeln"}) == "bestellungen-koeln-2"
    assert account_id_for("!!!", set()) == "postfach"


def test_connection_test_with_test_mailbox_reads_only(qapp: QApplication, bench: Workbench) -> None:
    base = Path(bench.database_path or "").parent / TEST_MAILBOX
    files = {p.name: p.read_bytes() for p in (base / "bestellungen").iterdir()}
    page = AccountsPage(bench, folder_sources(base), test_mailbox=True)
    page.reload(select="bestellungen")
    text = probe_and_wait(qapp, page)
    assert (
        f"Verbindung erfolgreich: {len(files)} Nachrichten" in text and "nichts verändert" in text
    )
    assert {p.name: p.read_bytes() for p in (base / "bestellungen").iterdir()} == files


# ---------- Echter IMAP-Server ----------


@pytest.fixture(scope="module")
def dovecot() -> Iterator[DovecotServer]:
    if REASON:
        pytest.skip(f"NOT TESTED – {REASON}")
    server = DovecotServer.create().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def imap_user(
    dovecot: DovecotServer, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    monkeypatch.setenv("SSL_CERT_FILE", str(dovecot.ca_file))
    digest = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:8]
    user = dovecot.add_user(f"gui{digest}")
    for uid, mail in enumerate(corpus(), start=1):
        dovecot.save(user, mail.raw)
        for flag in mail.flags:
            dovecot.add_flags(user, uid, flag)
    return user


def point_to(
    page: AccountsPage, server: DovecotServer, user: str, *, host: str = "localhost"
) -> None:
    page.reload(select="bestellungen")
    fill(page, host=host, port=server.imaps_port, username=user)


@needs_dovecot
def test_connection_test_against_real_server_reads_only(
    qapp: QApplication,
    bench: Workbench,
    store: MemoryCredentialStore,
    dovecot: DovecotServer,
    imap_user: str,
) -> None:
    before = dovecot.flags(imap_user)
    page = AccountsPage(bench, imap_sources(store))
    point_to(page, dovecot, imap_user)
    fill(page, password=PASSWORD)
    text = probe_and_wait(qapp, page)
    assert "Verbindung erfolgreich: 16 Nachrichten" in text
    assert dovecot.flags(imap_user) == before


@needs_dovecot
@pytest.mark.parametrize(
    ("host", "password", "expected"),
    [
        ("localhost", "Falsches-Passwort", "Anmeldung am Mailserver fehlgeschlagen"),
        ("127.0.0.1", PASSWORD, "Sichere Verbindung (TLS) fehlgeschlagen"),
    ],
)
def test_connection_test_errors_against_real_server(  # noqa: PLR0917 – pytest-Fixtures
    qapp: QApplication,
    bench: Workbench,
    store: MemoryCredentialStore,
    dovecot: DovecotServer,
    imap_user: str,
    host: str,
    password: str,
    expected: str,
) -> None:
    page = AccountsPage(bench, imap_sources(store))
    point_to(page, dovecot, imap_user, host=host)
    fill(page, password=password)
    text = probe_and_wait(qapp, page)
    assert expected in text and "imaplib" not in text and password not in text


def test_connection_test_timeout_fails_fast_with_one_attempt(
    qapp: QApplication,
    bench: Workbench,
    store: MemoryCredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def hold() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                accepted.append(listener.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    monkeypatch.setattr(fetching, "ImapMailSource", functools.partial(ImapMailSource, timeout=0.5))
    bench.set_password("bestellungen", PASSWORD)
    page = AccountsPage(bench, imap_sources(store))
    page.reload(select="bestellungen")
    fill(page, host="localhost", port=listener.getsockname()[1])
    page.save()
    try:
        started = time.monotonic()
        text = probe_and_wait(qapp, page)
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        thread.join(5)
        for conn in accepted:
            conn.close()
        listener.close()
    assert "Zeitüberschreitung" in text
    assert len(accepted) == 1 and elapsed < 5, "Verbindungstest wiederholt nicht"


@needs_dovecot
def test_complete_workflow_against_real_server(  # noqa: PLR0917 – pytest-Fixtures
    qapp: QApplication,
    bench: Workbench,
    store: MemoryCredentialStore,
    dovecot: DovecotServer,
    imap_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = dovecot.flags(imap_user)
    sources = imap_sources(store)

    # 1. Postfach konfigurieren (Passwort in den Anmeldespeicher) und Verbindung testen
    dialog = SettingsDialog(bench, mail_sources=sources)
    dialog.open_page("Postfächer")
    assert dialog.stack.currentWidget() is dialog.accounts
    page = dialog.accounts
    point_to(page, dovecot, imap_user)
    fill(page, password=PASSWORD)
    assert page.save() and store.get(credential_key("bestellungen")) == PASSWORD
    assert "Verbindung erfolgreich: 16 Nachrichten" in probe_and_wait(qapp, page)

    # 2. Aufträge abrufen
    summaries: list[FetchSummary] = []
    monkeypatch.setattr(
        fetch_ui.FetchSummaryDialog, "exec", lambda self: summaries.append(self.summary) or 0
    )
    window = MainWindow(bench, demo=True, persist=False, mail_sources=sources)
    window.show()
    qapp.processEvents()
    rows_before = window.table.source.rowCount()
    progress: list[str] = []
    window.fetcher.progress.connect(lambda p: progress.append(p.text))
    window.fetch_button.click()
    assert not window.fetch_button.isEnabled() and window.cancel_fetch_button.isVisible()
    wait_until(qapp, lambda: not window.fetcher.running and bool(summaries))

    # 3. Fortschritt, Ergebnis, Zähler, Auftrag öffnen
    summary = summaries[0]
    assert summary.ok and (summary.orders, summary.spam, summary.known) == (10, 2, 1)
    assert any(text.startswith("Nachricht 1 von") for text in progress)
    assert window.mail_status.text().endswith("10 neu") and window.fetch_button.isEnabled()
    window.categories.setCurrentIndex(0)
    qapp.processEvents()
    assert window.table.source.rowCount() == rows_before + 10
    window.table.select_order(summary.new_order_ids[0])
    qapp.processEvents()
    assert window.detail.snapshot is not None
    assert window.detail.snapshot.order.id == summary.new_order_ids[0]
    assert window.detail.overview.reference.text() == "GH-2026-2001"

    # 4. Postfach unverändert, zweiter Abruf ohne Duplikate
    assert dovecot.flags(imap_user) == before
    window.fetch_button.click()
    wait_until(qapp, lambda: not window.fetcher.running and len(summaries) == 2)
    assert summaries[1].ok and summaries[1].orders == 0 and summaries[1].known == 12
    window.close()


@needs_dovecot
def test_cancel_button_against_real_server(  # noqa: PLR0917 – pytest-Fixtures
    qapp: QApplication,
    bench: Workbench,
    store: MemoryCredentialStore,
    dovecot: DovecotServer,
    imap_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = dovecot.flags(imap_user)
    page = AccountsPage(bench, imap_sources(store))
    point_to(page, dovecot, imap_user)
    fill(page, password=PASSWORD)
    assert page.save()
    real = imap_sources(store)
    gate, waiting = threading.Event(), threading.Event()

    class Gated:
        def __init__(self, inner: ImapMailSource) -> None:
            self.inner, self.account_id, self.folder, self.downloads = (
                inner,
                inner.account_id,
                inner.folder,
                0,
            )

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

        def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
            self.downloads += 1
            if self.downloads == 3:
                waiting.set()
                gate.wait(30)
            return self.inner.fetch(uid, size, max_bytes)

        def abort(self) -> None:
            gate.set()
            self.inner.abort()

    summaries: list[FetchSummary] = []
    monkeypatch.setattr(
        fetch_ui.FetchSummaryDialog, "exec", lambda self: summaries.append(self.summary) or 0
    )
    window = MainWindow(
        bench, demo=True, persist=False, mail_sources=lambda a, h: Gated(real(a, h))
    )  # type: ignore[arg-type,return-value]
    window.show()
    window.fetch_button.click()
    wait_until(qapp, waiting.is_set)
    window.cancel_fetch_button.click()
    wait_until(qapp, lambda: not window.fetcher.running and bool(summaries))

    assert summaries[0].cancelled and summaries[0].orders == 2
    conn = bench._conn
    half = conn.execute(
        "SELECT COUNT(*) FROM mails WHERE state = 'extracted' AND uidvalidity != 1 "
        "AND id NOT IN (SELECT mail_id FROM orders)"
    ).fetchone()[0]
    assert half == 0 and dovecot.flags(imap_user) == before

    window.mail_sources = real
    window.fetch_button.click()
    wait_until(qapp, lambda: not window.fetcher.running and len(summaries) == 2)
    assert summaries[1].ok and (summaries[1].orders, summaries[1].known) == (8, 3)
    assert dovecot.flags(imap_user) == before
    window.close()
