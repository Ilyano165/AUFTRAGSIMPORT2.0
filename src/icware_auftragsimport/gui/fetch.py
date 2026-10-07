"""Aufträge abrufen in der Oberfläche: Hintergrund-Thread, Steuerung, Zusammenfassung.

Der Abruf läuft vollständig in einem eigenen ``QThread``: Netzwerk, MIME, Erkennung und
Datenbankzugriffe (mit eigener Verbindung). Der GUI-Thread erhält nur Fortschritt und
Ergebnis über Signale und bleibt jederzeit bedienbar.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..app.fetching import FetchJob
from ..app.presentation import Tone
from ..infrastructure.logging_setup import get_logger
from ..services.mail_fetch import (
    CancelToken,
    FetchErrorKind,
    FetchFailure,
    FetchProgress,
    FetchSummary,
    MailFetchService,
)
from .widgets import Notice, muted

_log = get_logger("abruf")
SETTINGS_KINDS = frozenset(
    {
        FetchErrorKind.CONFIGURATION,
        FetchErrorKind.CREDENTIALS,
        FetchErrorKind.AUTHENTICATION,
        FetchErrorKind.MAILBOX,
        FetchErrorKind.UNSUPPORTED_AUTH,
        FetchErrorKind.TLS,
    }
)
UNTOUCHED = (
    "Im Postfach wurde nichts verändert: keine Mail als gelesen markiert, nichts verschoben "
    "oder gelöscht."
)


class _Worker(QObject):
    """Lebt im Hintergrund-Thread und führt genau eine Aufgabe aus."""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, task: Callable[[Callable[[object], None]], object]) -> None:
        super().__init__()
        self._task = task

    @Slot()
    def run(self) -> None:
        """Führt die Aufgabe aus; jede Ausnahme wird zum Ergebnis, nie zum stillen Ende."""
        try:
            result: object = self._task(self.progress.emit)
        except Exception as exc:
            _log.exception("Hintergrundaufgabe fehlgeschlagen")
            result = exc
        self.finished.emit(result)
        # Der Thread beendet seine Ereignisschleife selbst. Sonst käme quit() erst über die
        # Warteschlange des GUI-Threads, und ein wartender GUI-Thread (Fenster schließen)
        # würde bis zur Zeitüberschreitung blockieren.
        thread = QThread.currentThread()
        if thread is not None:
            thread.quit()


class BackgroundTask(QObject):
    """Führt eine Funktion in einem eigenen ``QThread`` aus; Ergebnis kommt im GUI-Thread an."""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _Worker | None = None

    @property
    def running(self) -> bool:
        """Läuft gerade eine Aufgabe?"""
        return self._thread is not None

    def start(self, task: Callable[[Callable[[object], None]], object]) -> bool:
        """Startet die Aufgabe; ``False``, wenn bereits eine läuft."""
        if self._thread is not None:
            return False
        thread = QThread(self)
        worker = _Worker(task)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.progress)
        worker.finished.connect(self._finished)
        self._thread, self._worker = thread, worker
        thread.start()
        return True

    def _finished(self, result: object) -> None:
        # quit direkt (thread-sicher): eine Warteschlangen-Verbindung käme erst nach diesem
        # Aufruf an, und wait() würde den GUI-Thread blockieren.
        thread = self._thread
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        self._thread = self._worker = None
        self.finished.emit(result)

    def wait(self, milliseconds: int) -> bool:
        """Wartet auf das Ende (etwa beim Schließen des Fensters)."""
        return self._thread.wait(milliseconds) if self._thread is not None else True


class FetchState(StrEnum):
    """Zustand des Postfachs aus Sicht des Benutzers."""

    NOT_CONNECTED = "not_connected"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FetchController(QObject):
    """Startet, verfolgt und bricht Abrufe ab; nie mehr als einer gleichzeitig."""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._task = BackgroundTask(self)
        self._task.progress.connect(self.progress)
        self._task.finished.connect(self._done)
        self._token: CancelToken | None = None
        self._service: MailFetchService | None = None

    @property
    def running(self) -> bool:
        """Läuft ein Abruf?"""
        return self._task.running

    def start(self, job: FetchJob) -> bool:
        """Startet den Abruf im Hintergrund-Thread."""
        if self.running:
            return False
        token = CancelToken()
        self._token, self._service = token, None

        def register(service: MailFetchService) -> None:
            self._service = service

        return self._task.start(lambda emit: job.run(token, emit, register))

    def cancel(self) -> None:
        """Sicherer Abbruch: zwischen zwei Mails, laufende Netzwerkoperation wird beendet."""
        if self._token is not None:
            self._token.cancel()
        service = self._service
        if service is not None:
            service.abort()

    def wait(self, milliseconds: int) -> bool:
        """Wartet auf das Ende des Abrufs."""
        return self._task.wait(milliseconds)

    def _done(self, result: object) -> None:
        self._token, self._service = None, None
        if isinstance(result, FetchSummary):
            summary = result
        else:
            summary = FetchSummary(failure=FetchFailure(FetchErrorKind.UNEXPECTED, ""))
        self.finished.emit(summary)


def state_of(summary: FetchSummary) -> FetchState:
    """Zustand nach einem Abruf."""
    if summary.failure is not None:
        return FetchState.FAILED
    return FetchState.CANCELLED if summary.cancelled else FetchState.DONE


class FetchSummaryDialog(QDialog):
    """Zusammenfassung nach dem Abruf: nur Zahlen und verständliche Hinweise."""

    def __init__(self, summary: FetchSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self.open_settings = False
        state = state_of(summary)
        title = {
            FetchState.DONE: "Abruf abgeschlossen",
            FetchState.CANCELLED: "Abruf abgebrochen",
            FetchState.FAILED: "Abruf fehlgeschlagen",
        }[state]
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("DetailTitle")
        layout.addWidget(heading)
        if summary.failure is not None:
            self.failure_notice = Notice()
            self.failure_notice.show_text(
                Tone.DANGER, f"{summary.failure.what}. {summary.failure.action}"
            )
            layout.addWidget(self.failure_notice)
        self.lines: list[QLabel] = []
        if summary.checked or summary.failure is None:
            for text in summary.lines():
                label = QLabel(text)
                if text.startswith("davon"):
                    label.setIndent(16)
                self.lines.append(label)
                layout.addWidget(label)
        if summary.problems:
            counts = Counter(problem.kind for problem in summary.problems)
            text = "\n".join(f"{n} × {kind.label}. {kind.action}" for kind, n in counts.items())
            problems = Notice()
            problems.show_text(Tone.WARNING, text)
            layout.addWidget(problems)
        if not summary.ok and summary.orders:
            layout.addWidget(muted("Bis dahin abgerufene Aufträge sind vollständig gespeichert."))
        note = muted(UNTOUCHED)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if summary.failure is not None and summary.failure.kind in SETTINGS_KINDS:
            settings = QPushButton("Postfach-Einstellungen")
            settings.clicked.connect(self._settings)
            buttons.addWidget(settings)
        close = QPushButton("Schließen")
        close.setProperty("role", "primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def text(self) -> str:
        """Gesamter sichtbarer Text (für Tests und „kopieren“)."""
        return "\n".join(label.text() for label in self.findChildren(QLabel))

    def _settings(self) -> None:
        self.open_settings = True
        self.accept()


def progress_text(progress: FetchProgress) -> str:
    """Statuszeile während des Abrufs."""
    return f"Abruf läuft … {progress.text}"
