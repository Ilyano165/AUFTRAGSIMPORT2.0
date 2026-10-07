"""Start der Anwendung: Produktivbetrieb, Demo, ``--version`` und ``--self-test``."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, Qt, QTimer, QTranslator
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from ..app.demo import build_demo
from ..app.fetching import folder_sources, imap_sources
from ..app.testmails import TEST_MAILBOX
from ..app.workbench import Workbench
from ..branding import LOGGER_NAME, PRODUCT_NAME, VERSION
from ..buildinfo import current
from ..config.policy import MachinePolicy, load_policy
from ..config.store import ConfigStore
from ..domain.errors import AppError, ConfigError, InstanceLockedError
from ..infrastructure.clock import SystemClock
from ..infrastructure.db import MIGRATIONS, Database
from ..infrastructure.lock import InstanceLock
from ..infrastructure.logging_setup import configure_logging
from ..infrastructure.paths import AppPaths, migrate_legacy_layout
from ..ingest.imap import tls_context
from ..security.crash import install_crash_handler
from ..security.credentials import (
    CredentialStore,
    KeyringCredentialStore,
    LazyCredentialStore,
    credential_key,
)
from ..security.redaction import register_secret
from ..security.runtime import check_runtime
from ..services.mail_fetch import CancelToken, FetchFailure
from .crash_dialog import CrashReporter
from .main_window import MainWindow
from .theme import BASE_POINT_SIZE, FONT_FAMILIES, TOKENS, stylesheet

APP_USER_MODEL_ID = "IC-Ware.AuftragsImport.2"
EXIT_CONFIG, EXIT_RUNTIME = 2, 3
_log = logging.getLogger(f"{LOGGER_NAME}.start")


def configure_high_dpi() -> None:
    """Vor dem Anlegen der Anwendung: gebrochene Skalierung (125 %, 150 %) ohne Rundung."""
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


def set_windows_identity() -> None:
    """Eigene Taskleistengruppe und eigenes Symbol unter Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return


def palette() -> QPalette:
    """Systemunabhängige helle Palette passend zu den Tokens."""
    colors = QPalette()
    roles = {
        QPalette.ColorRole.Window: TOKENS.window,
        QPalette.ColorRole.WindowText: TOKENS.text,
        QPalette.ColorRole.Base: TOKENS.surface,
        QPalette.ColorRole.AlternateBase: TOKENS.surface_alt,
        QPalette.ColorRole.Text: TOKENS.text,
        QPalette.ColorRole.Button: TOKENS.surface,
        QPalette.ColorRole.ButtonText: TOKENS.text,
        QPalette.ColorRole.Highlight: TOKENS.accent,
        QPalette.ColorRole.HighlightedText: TOKENS.accent_text,
        QPalette.ColorRole.ToolTipBase: TOKENS.surface,
        QPalette.ColorRole.ToolTipText: TOKENS.text,
        QPalette.ColorRole.PlaceholderText: TOKENS.text_secondary,
        QPalette.ColorRole.Link: TOKENS.accent,
    }
    for role, value in roles.items():
        colors.setColor(role, QColor(value))
    colors.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TOKENS.text_disabled)
    )
    colors.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TOKENS.text_disabled)
    )
    return colors


def install_german_translation(app: QApplication) -> bool:
    """Deutsche Qt-Standardtexte („Speichern“, „Abbrechen“) in Rückfragen und Dateidialogen."""
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(
        QLocale(QLocale.Language.German, QLocale.Country.Germany), "qtbase", "_", folder
    ):
        app.installTranslator(translator)
        return True
    return False


def create_application(argv: Sequence[str]) -> QApplication:
    """QApplication mit Produktaussehen."""
    configure_high_dpi()
    set_windows_identity()
    app = QApplication(list(argv))
    app.setOrganizationName("IC-Ware")
    app.setApplicationName("Auftrags-Import")
    app.setApplicationDisplayName(PRODUCT_NAME)
    app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")
    app.setPalette(palette())
    font = QFont()
    font.setFamilies([*FONT_FAMILIES, app.font().family()])
    font.setPointSizeF(BASE_POINT_SIZE)
    app.setFont(font)
    app.setStyleSheet(stylesheet())
    install_german_translation(app)
    return app


def demo_workbench(directory: Path | None = None, now: datetime | None = None) -> Workbench:
    """Workbench mit Demobestand."""
    moment = now or datetime.now(UTC).astimezone()
    target = directory or Path(tempfile.mkdtemp(prefix="icware-demo-"))
    database, settings = build_demo(target, moment)
    return Workbench(database.connect(), settings, SystemClock(), data_dir=target)


FAILURE_TEST_SECRET = "Fehlertest-Geheimnis-7Q2x"
FAILURE_TEST_DELAY_MS = 1500


class ControlledTestError(RuntimeError):
    """Absichtlicher Fehler für ``--fehlertest`` (Fehlerdialog und Fehlerbericht prüfen)."""


def controlled_failure() -> None:
    """Wirft einen Fehler mit präparierten sensiblen Daten.

    Der Fehlerbericht darf weder das Geheimnis noch den Benutzerpfad enthalten; das Programm
    muss danach weiter bedienbar sein.
    """
    register_secret(FAILURE_TEST_SECRET)
    raise ControlledTestError(
        f"Kontrollierter Testfehler (--fehlertest): Passwort {FAILURE_TEST_SECRET} "
        f"für test.kunde@example.de in {Path.home() / 'Dokumente'}"
    )


def _fail(title: str, text: str, code: int) -> int:
    _log.error("%s: %s", title, text)
    QMessageBox.critical(None, title, text)
    return code


def run_production(app: QApplication, *, failure_test: bool = False) -> int:
    """Produktivbetrieb mit den Windows-Ablageorten."""
    paths = AppPaths.default()
    paths.ensure()
    moved = migrate_legacy_layout(paths)
    policy, policy_issues = load_policy(paths.policy_file)
    configure_logging(paths.log_dir, policy.log_level or "INFO")
    info = current()
    _log.info("Start %s %s", PRODUCT_NAME, info.describe())
    for item in moved:
        _log.info("Aus der früheren Ablage übernommen: %s", item)
    for issue in policy_issues:
        _log.warning("Richtlinie: %s", issue)
    reporter = CrashReporter(policy.support_contact, app)
    install_crash_handler(paths.crash_dir, build=info.describe(), notify=reporter.notify)
    runtime = check_runtime()
    if not runtime.secure:
        return _fail("Unsichere Laufzeitumgebung", "\n".join(runtime.problems), EXIT_RUNTIME)
    lock = InstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except InstanceLockedError:
        QMessageBox.information(None, PRODUCT_NAME, f"{PRODUCT_NAME} ist bereits geöffnet.")
        return 0
    try:
        return _open_main_window(app, paths, policy, failure_test=failure_test)
    finally:
        lock.release()


def _open_main_window(
    app: QApplication, paths: AppPaths, policy: MachinePolicy, *, failure_test: bool = False
) -> int:
    clock = SystemClock()
    store = ConfigStore(paths.config_file, paths.backup_dir, clock)
    try:
        settings = store.load()
        if not paths.config_file.exists():
            store.save(settings)
    except ConfigError as exc:
        return _fail(
            "Einstellungen fehlerhaft", f"{exc}\n\nSicherungen: {paths.backup_dir}", EXIT_CONFIG
        )
    database = Database(paths.database_file)
    conn = database.connect()
    database.migrate(conn)
    credentials = LazyCredentialStore()
    workbench = Workbench(
        conn, settings, clock, store=store, data_dir=paths.root, credentials=credentials
    )
    window = MainWindow(
        workbench, demo=False, paths=paths, policy=policy, mail_sources=imap_sources(credentials)
    )
    window.show()
    if failure_test:
        _log.warning("Kontrollierter Testfehler angefordert (--fehlertest)")
        QTimer.singleShot(FAILURE_TEST_DELAY_MS, controlled_failure)
    return app.exec()


Check = tuple[str, bool | None, str]
SELFTEST_ACCOUNT = "__selbsttest__"


def credential_check(
    factory: Callable[[], CredentialStore] = KeyringCredentialStore,
    *,
    windows: bool = sys.platform == "win32",
) -> Check:
    """Anmeldespeicher: Testeintrag speichern, lesen, löschen (keine echten Zugangsdaten).

    Unter Windows ist ein fehlender Speicher ein Fehler; anderswo nicht anwendbar (``None``).
    """
    name = "Anmeldespeicher"
    try:
        store = factory()
        key = credential_key(SELFTEST_ACCOUNT)
        value = secrets.token_urlsafe(24)
        store.set(key, value)
        readable = store.get(key) == value
        store.delete(key)
        removed = store.get(key) is None
    except AppError as exc:
        if not windows:
            return name, None, "nicht geprüft (nur unter Windows)"
        return name, False, exc.message.why
    backend = getattr(store, "backend_name", type(store).__name__)
    detail = f"{backend}: speichern, lesen, löschen"
    return name, readable and removed, detail if readable and removed else f"{backend}: fehlerhaft"


def tls_check() -> Check:
    """Stammzertifikate des Systems sind für die IMAP-Verbindung ladbar."""
    count = int(tls_context().cert_store_stats().get("x509_ca", 0))
    return "TLS-Zertifikatsspeicher", count > 0, f"{count} Stammzertifikate"


def fetch_check(folder: Path) -> Check:
    """Kompletter Abruf aus dem Demo-Testpostfach (MIME, Vorprüfung, Erkennung, Speicherung)."""
    name = "Mail-Abruf (Testpostfach)"
    workbench = demo_workbench(folder)
    job = workbench.fetch_job(folder_sources(folder / TEST_MAILBOX))
    if isinstance(job, FetchFailure):
        return name, False, job.what
    summary = job.run(CancelToken(), lambda _progress: None)
    ok = summary.ok and summary.orders > 0 and summary.spam > 0 and summary.failed == 0
    detail = f"{summary.checked} geprüft, {summary.orders} Aufträge, {summary.spam} Spam"
    return name, ok, detail if summary.failure is None else summary.failure.what


def self_test(output: Path | None = None) -> int:
    """Prüft das fertige Programm ohne Fenster: Laufzeit, Ressourcen, Datenbank, Anmeldespeicher,
    TLS, Mail-Abruf, Oberfläche."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    results: list[Check] = []
    runtime = check_runtime()
    results.append(
        (
            "Laufzeit",
            runtime.secure,
            f"Python {runtime.python}, expat {runtime.expat}, {runtime.openssl}",
        )
    )
    try:
        from ..export.lexware.spec import load_spec  # noqa: PLC0415

        results.append(("Lexware-Spezifikation", True, load_spec().__class__.__name__))
    except (AppError, OSError, ValueError) as exc:
        results.append(("Lexware-Spezifikation", False, type(exc).__name__))
    with tempfile.TemporaryDirectory(prefix="icware-selbsttest-") as folder:
        database = Database(Path(folder) / "test.db")
        conn = database.connect()
        version = database.migrate(conn)
        conn.close()
        results.append(("Datenbank", version == len(MIGRATIONS), f"Schema {version}"))
        results.append(credential_check())
        results.append(tls_check())
        results.append(fetch_check(Path(folder) / "abruf"))
        app = create_application([sys.argv[0]])
        cancel = QCoreApplication.translate("QPlatformTheme", "Cancel")
        results.append(("Deutsche Qt-Texte", cancel == "Abbrechen", cancel))
        window = MainWindow(demo_workbench(Path(folder) / "demo"), demo=True, persist=False)
        window.resize(1280, 720)
        image = window.grab()
        results.append(("Oberfläche", not image.isNull(), f"{image.width()}×{image.height()}"))
        window.close()
        app.processEvents()
    marks = {True: "OK    ", False: "FEHLER", None: "–     "}
    lines = [f"{marks[ok]} {name}: {detail}" for name, ok, detail in results]
    _emit("\n".join(lines), output)
    return 1 if any(ok is False for _, ok, _ in results) else 0


def _emit(text: str, output: Path | None) -> None:
    """Ausgabe; die Fenster-EXE hat keine Konsole, deshalb optional in eine Datei."""
    if output is not None:
        output.write_text(text + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(text)


def main(argv: Sequence[str] | None = None) -> int:
    """Einstiegspunkt der Oberfläche."""
    parser = argparse.ArgumentParser(prog="AuftragsImport", description=PRODUCT_NAME)
    parser.add_argument("--demo", action="store_true", help="mit Demodaten starten")
    parser.add_argument("--version", action="store_true", help="Version und Build ausgeben")
    parser.add_argument("--self-test", action="store_true", help="Selbsttest ohne Fenster")
    parser.add_argument("--output", type=Path, help="Ergebnis von --version/--self-test in Datei")
    parser.add_argument(
        "--testpostfach",
        type=Path,
        metavar="ORDNER",
        help="nur mit --demo: .eml-Dateien aus ORDNER statt des Demo-Testpostfachs abrufen",
    )
    parser.add_argument(
        "--fehlertest",
        action="store_true",
        help="kontrollierter Testfehler nach dem Start (Fehlerdialog prüfen; ohne --demo)",
    )
    args, qt_args = parser.parse_known_args(list(argv if argv is not None else sys.argv[1:]))
    if args.version:
        _emit(f"{PRODUCT_NAME} {current().describe()}", args.output)
        return 0
    if args.self_test:
        return self_test(args.output)
    if args.testpostfach is not None and not args.demo:
        parser.error("--testpostfach ist nur zusammen mit --demo erlaubt (getrennter Bestand)")
    if args.fehlertest and args.demo:
        parser.error("--fehlertest prüft den Fehlerdialog des Produktivbetriebs; nicht mit --demo")
    app = create_application([sys.argv[0], *qt_args])
    if not args.demo:
        return run_production(app, failure_test=args.fehlertest)
    target = Path(tempfile.mkdtemp(prefix="icware-demo-"))
    sources = (
        folder_sources(args.testpostfach, shared=True)
        if args.testpostfach is not None
        else folder_sources(target / TEST_MAILBOX)
    )
    window = MainWindow(demo_workbench(target), demo=True, mail_sources=sources)
    window.resize(1360, 820)
    window.show()
    return app.exec()
