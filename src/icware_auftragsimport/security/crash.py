"""Fehlerberichte ohne Geheimnisse, ohne Variableninhalte, ohne Benutzernamen.

Berichte enthalten Fehler-ID, Zeitpunkt, Build, System, Ausnahmetyp, bereinigte Meldung und
Aufrufstapel (Datei, Zeile, Funktion); niemals lokale Variablen. Jede Zeile wird gekürzt,
bereinigt und von Benutzerpfaden befreit. Native Abstürze (etwa in Qt) landen über
``faulthandler`` in einer eigenen Datei.
"""

from __future__ import annotations

import faulthandler
import logging
import platform
import secrets
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

from ..branding import LOGGER_NAME
from ..infrastructure.atomic import atomic_write_text
from .redaction import anonymize_paths, redact

MAX_LINE_CHARS = 300
KEEP_REPORTS = 50
ERROR_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
NATIVE_LOG = "native-abstuerze.log"
_state: dict[str, TextIO] = {}
_log = logging.getLogger(f"{LOGGER_NAME}.crash")


@dataclass(frozen=True, slots=True)
class CrashRecord:
    """Ein erfasster Fehler; nur diese Angaben sieht der Benutzer."""

    error_id: str
    occurred: datetime
    report: Path | None


CrashNotifier = Callable[[CrashRecord], None]


def new_error_id(now: datetime | None = None) -> str:
    """Kurze, eindeutige und am Telefon gut buchstabierbare ID, etwa ``E-261001-7KQ2XM``."""
    moment = now or datetime.now(UTC)
    token = "".join(secrets.choice(ERROR_ID_ALPHABET) for _ in range(6))
    return f"E-{moment:%y%m%d}-{token}"


def crash_report(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    error_id: str = "",
    occurred: datetime | None = None,
    build: str = "",
) -> str:
    """Bereinigter Bericht; ``format_exception`` gibt nie lokale Variablen aus."""
    header = [
        line
        for line in (
            f"Fehler-ID: {error_id}" if error_id else "",
            f"Zeitpunkt: {occurred:%Y-%m-%d %H:%M:%S} UTC" if occurred else "",
            f"Build: {build}" if build else "",
            f"System: {platform.platform(terse=True)}, Python {platform.python_version()}",
        )
        if line
    ]
    cleaned = [
        anonymize_paths(redact(line[: MAX_LINE_CHARS * 2]))[:MAX_LINE_CHARS]
        for line in "".join(traceback.format_exception(exc_type, exc, tb)).splitlines()
    ]
    return "\n".join([*header, "", *cleaned]) + "\n"


def _prune(directory: Path) -> None:
    reports = sorted(
        directory.glob("absturz-*.txt"), key=lambda p: p.stat().st_mtime_ns, reverse=True
    )
    for old in reports[KEEP_REPORTS:]:
        old.unlink(missing_ok=True)


def write_crash_report(
    directory: Path,
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    build: str = "",
) -> CrashRecord:
    """Schreibt den Bericht atomar; scheitert das Schreiben, bleibt wenigstens die Fehler-ID."""
    occurred = datetime.now(UTC)
    error_id = new_error_id(occurred)
    path: Path | None = directory / f"absturz-{error_id}.txt"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        text = crash_report(exc_type, exc, tb, error_id=error_id, occurred=occurred, build=build)
        atomic_write_text(path, text)  # type: ignore[arg-type]
        _prune(directory)
    except OSError:
        path = None
    return CrashRecord(error_id, occurred, path)


def _enable_native(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / NATIVE_LOG).open("a", encoding="utf-8")
    except OSError:
        return
    if faulthandler.is_enabled():
        faulthandler.disable()
    previous = _state.pop("native", None)
    if previous is not None:
        previous.close()
    faulthandler.enable(file=handle, all_threads=True)
    _state["native"] = handle


def install_crash_handler(
    directory: Path, *, build: str = "", notify: CrashNotifier | None = None, native: bool = True
) -> None:
    """Unbehandelte Ausnahmen (alle Threads) werden zu Berichten und Benachrichtigungen."""

    def handle(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        record = write_crash_report(directory, exc_type, exc, tb, build=build)
        _log.error("Unbehandelter Fehler %s (%s)", record.error_id, exc_type.__name__)
        if notify is not None:
            try:
                notify(record)
            except Exception:
                _log.error("Fehlerdialog für %s konnte nicht angezeigt werden", record.error_id)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and not issubclass(args.exc_type, SystemExit):
            handle(args.exc_type, args.exc_value, args.exc_traceback)

    def unraisable(args: sys.UnraisableHookArgs) -> None:
        _log.warning("Nicht behandelbarer Fehler beim Aufräumen: %s", args.exc_type.__name__)

    sys.excepthook = handle
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable
    if native:
        _enable_native(directory)
