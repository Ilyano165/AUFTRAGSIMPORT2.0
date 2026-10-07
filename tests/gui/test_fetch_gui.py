"""Oberfläche „Aufträge abrufen“ mit echtem QThread: Bedienbarkeit, Fortschritt, Abbruch,
Zähler, ungespeicherte Änderungen. Die Arbeit läuft wirklich im Hintergrund-Thread."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fetchkit import FakeSource, build_mail, imap_error, messages, order_text
from icware_auftragsimport.app.fetching import folder_sources
from icware_auftragsimport.app.testmails import TEST_MAILBOX
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.config.schema import MailAccount
from icware_auftragsimport.domain.models import Order
from icware_auftragsimport.gui import fetch as fetch_ui
from icware_auftragsimport.gui.fetch import FetchState
from icware_auftragsimport.gui.main_window import SHORTCUTS, MainWindow
from icware_auftragsimport.infrastructure.repositories import OrderRepository
from icware_auftragsimport.ingest.imap import ImapErrorKind
from icware_auftragsimport.services.mail_fetch import FetchSummary, SourceHooks


class Shown(list[FetchSummary]):
    """Mitgeschnittene Zusammenfassungsdialoge (statt modal angezeigt)."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def text(self, index: int = 0) -> str:
        return self.texts[index]


def wait_until(qapp: QApplication, condition: Callable[[], bool], seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("Zeitüberschreitung beim Warten auf die Oberfläche")
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> Shown:
    dialogs = Shown()

    def record(self: fetch_ui.FetchSummaryDialog) -> int:
        dialogs.append(self.summary)
        dialogs.texts.append(self.text())
        return 0

    monkeypatch.setattr(fetch_ui.FetchSummaryDialog, "exec", record)
    return dialogs


def make_window(
    qapp: QApplication, workbench: Workbench, sources: Callable[..., object] | None
) -> MainWindow:
    window = MainWindow(workbench, demo=True, persist=False, mail_sources=sources)  # type: ignore[arg-type]
    window.resize(1536, 824)
    window.show()
    qapp.processEvents()
    return window


@pytest.fixture
def close_later() -> Iterator[list[MainWindow]]:
    windows: list[MainWindow] = []
    yield windows
    for window in windows:
        window.detail.dirty = False
        window.close()


class GatedSource(FakeSource):
    """Blockiert den Hintergrund-Thread beim Download einer UID, bis das Tor öffnet."""

    def __init__(self, *args: object, gate_uid: int = 1, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.gate = threading.Event()
        self.waiting = threading.Event()
        self.gate_uid = gate_uid

    def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
        if uid == self.gate_uid:
            self.waiting.set()
            self.gate.wait(30)
        return super().fetch(uid, size, max_bytes)

    def abort(self) -> None:
        super().abort()
        self.gate.set()


def fixed(source: FakeSource) -> Callable[[MailAccount, SourceHooks], FakeSource]:
    return lambda _account, _hooks: source


def count_of(window: MainWindow, label: str) -> int:
    for i in range(window.categories.count()):
        text = window.categories.tabText(i)
        if text.startswith(label):
            return int(text.split()[-1]) if text.split()[-1].isdigit() else 0
    raise AssertionError(label)


def three_orders() -> FakeSource:
    raws = [build_mail(order_text(f"GH-{i}"), message_id=f"<g{i}@x>") for i in (1, 2, 3)]
    return GatedSource(messages(*raws), gate_uid=2)


# ---------- Ausgangszustand ----------


def test_initial_state_and_shortcut(
    qapp: QApplication, workbench: Workbench, close_later: list[MainWindow]
) -> None:
    window = make_window(qapp, workbench, folder_sources(Path("/nicht/vorhanden")))
    close_later.append(window)
    assert window.fetch_button.text() == "Aufträge abrufen" and window.fetch_button.isEnabled()
    assert not window.cancel_fetch_button.isVisible() and not window.fetch_progress.isVisible()
    assert window.mail_status.text() == "Postfach: nicht verbunden"
    assert ("F5", "Aufträge abrufen") in SHORTCUTS


# ---------- Hintergrund, Bedienbarkeit, Fortschritt ----------


def test_fetch_runs_in_background_and_ui_stays_responsive(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    source = three_orders()
    window = make_window(qapp, workbench, fixed(source))
    close_later.append(window)
    rows_before = window.table.source.rowCount()

    window.fetch_button.click()
    wait_until(qapp, source.waiting.is_set)

    assert window.fetch_state is FetchState.RUNNING
    assert not window.fetch_button.isEnabled() and window.fetch_button.text() == "Abruf läuft …"
    assert window.cancel_fetch_button.isVisible() and window.fetch_progress.isVisible()
    assert window.mail_status.text().startswith("Postfach: Abruf läuft")
    ticks: list[int] = []
    QTimer.singleShot(0, lambda: ticks.append(1))
    window.categories.setCurrentIndex(1)
    wait_until(qapp, lambda: bool(ticks), seconds=2)
    assert window.categories.currentIndex() == 1, "Oberfläche reagiert während des Abrufs"
    window.fetch()
    assert window.fetcher.running and len(source.threads) == 2, "kein zweiter Abruf parallel"
    assert threading.get_ident() not in source.threads, "Abruf lief im GUI-Thread"

    source.gate.set()
    wait_until(qapp, lambda: not window.fetcher.running and bool(shown))

    assert window.fetch_state is FetchState.DONE and window.fetch_button.isEnabled()
    assert not window.cancel_fetch_button.isVisible() and not window.fetch_progress.isVisible()
    assert window.mail_status.text().endswith("3 neu")
    assert shown[0].orders == 3 and "3 Bestellungen erkannt" in shown.text()
    window.categories.setCurrentIndex(0)
    qapp.processEvents()
    assert window.table.source.rowCount() == rows_before + 3


def test_counters_update_and_new_order_can_be_opened(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    base = Path(workbench.database_path or "").parent / TEST_MAILBOX
    window = make_window(qapp, workbench, folder_sources(base))
    close_later.append(window)
    review_before = count_of(window, "Prüfung")

    window.fetch()
    wait_until(qapp, lambda: not window.fetcher.running and bool(shown))

    summary = shown[0]
    assert summary.ok and summary.orders == 4 and summary.known == 1
    assert count_of(window, "Prüfung") == review_before + summary.review
    new_id = summary.new_order_ids[-1]
    window.table.select_order(new_id)
    qapp.processEvents()
    assert window.detail.snapshot is not None and window.detail.snapshot.order.id == new_id


# ---------- Abbruch ----------


def test_cancel_button_stops_cleanly_and_next_fetch_completes(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    source = three_orders()
    window = make_window(qapp, workbench, fixed(source))
    close_later.append(window)
    window.fetch()
    wait_until(qapp, source.waiting.is_set)

    window.cancel_fetch_button.click()
    assert window.mail_status.text() == "Postfach: Abruf wird abgebrochen …"
    assert not window.cancel_fetch_button.isEnabled()
    wait_until(qapp, lambda: not window.fetcher.running and bool(shown))

    assert window.fetch_state is FetchState.CANCELLED and shown[0].cancelled
    assert shown[0].orders == 1 and source.aborted
    assert "Abruf abgebrochen" in shown.text()
    stored = [
        o.id
        for o in workbench_orders(workbench)
        if o.customer_reference.value in ("GH-1", "GH-2", "GH-3")
    ]
    assert len(stored) == 1

    source.gate_uid = 0
    window.fetch()
    wait_until(qapp, lambda: not window.fetcher.running and len(shown) == 2)
    assert shown[1].ok and (shown[1].orders, shown[1].known) == (2, 1)
    refs = sorted(o.customer_reference.value or "" for o in workbench_orders(workbench))
    assert [r for r in refs if r.startswith("GH-")] == ["GH-1", "GH-2", "GH-3"]


def workbench_orders(workbench: Workbench) -> list[Order]:
    conn = workbench._conn
    ids = [row[0] for row in conn.execute("SELECT id FROM orders")]
    return [OrderRepository(conn).get(order_id) for order_id in ids]


def test_closing_window_during_fetch_cancels_and_waits(
    qapp: QApplication, workbench: Workbench, shown: Shown
) -> None:
    source = three_orders()
    window = make_window(qapp, workbench, fixed(source))
    window.fetch()
    wait_until(qapp, source.waiting.is_set)
    started = time.monotonic()
    window.close()
    assert time.monotonic() - started < 5 and source.aborted
    assert not window.fetcher.running or window.fetcher.wait(5000)


# ---------- Ungespeicherte Änderungen ----------


def test_unsaved_edits_survive_a_fetch(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    window = make_window(
        qapp, workbench, fixed(FakeSource(messages(build_mail(message_id="<e@x>"))))
    )
    close_later.append(window)
    order_id = next(r.order_id for r in workbench.rows() if workbench.snapshot(r.order_id).editable)
    window.table.select_order(order_id)
    qapp.processEvents()
    before = workbench.snapshot(order_id).order.customer_reference.value
    field = window.detail.overview.reference
    field.setFocus()
    QTest.keyClicks(field, "-GEAENDERT")
    assert window.detail.dirty

    window.fetch()
    wait_until(qapp, lambda: not window.fetcher.running and bool(shown))

    assert shown[0].orders == 1
    assert window.detail.dirty, "ungespeicherte Änderung wurde verworfen"
    assert window.detail.snapshot is not None and window.detail.snapshot.order.id == order_id
    assert field.text().endswith("-GEAENDERT")
    assert workbench.snapshot(order_id).order.customer_reference.value == before


# ---------- Fehler ----------


def test_connection_error_is_understandable(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    source = FakeSource(
        messages(build_mail()),
        fail={
            "open": imap_error(
                ImapErrorKind.AUTHENTICATION, "imaplib.error: [AUTHENTICATIONFAILED]"
            )
        },
    )
    window = make_window(qapp, workbench, fixed(source))
    close_later.append(window)
    window.fetch()
    wait_until(qapp, lambda: not window.fetcher.running and bool(shown))
    assert window.fetch_state is FetchState.FAILED
    assert window.mail_status.text().startswith("Postfach: Abruf fehlgeschlagen")
    text = shown.text()
    assert "Anmeldung am Mailserver fehlgeschlagen" in text and "Postfächer" in text
    assert "imaplib" not in text and "AUTHENTICATIONFAILED" not in text and "Traceback" not in text


def test_unconfigured_profile_explains_instead_of_starting(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    profile = replace(workbench.profile, mail_account_id="")
    workbench.save_profile(profile)
    window = make_window(qapp, workbench, fixed(FakeSource({})))
    close_later.append(window)
    window.fetch()
    qapp.processEvents()
    assert not window.fetcher.running and window.fetch_state is FetchState.NOT_CONNECTED
    assert "Postfach nicht vollständig eingerichtet" in shown.text()


def test_unexpected_worker_exception_becomes_a_failure(
    qapp: QApplication, workbench: Workbench, shown: Shown, close_later: list[MainWindow]
) -> None:
    def broken(_account: MailAccount, _hooks: SourceHooks) -> FakeSource:
        raise RuntimeError("kaputt")

    window = make_window(qapp, workbench, broken)
    close_later.append(window)
    window.fetch()
    wait_until(qapp, lambda: not window.fetcher.running and bool(shown))
    assert window.fetch_state is FetchState.FAILED and window.fetch_button.isEnabled()
    assert "Unerwarteter Fehler beim Abruf" in shown.text() and "kaputt" not in shown.text()
