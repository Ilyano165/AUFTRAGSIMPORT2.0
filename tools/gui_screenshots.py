"""Rendert die Oberfläche in mehreren Skalierungen und Fenstergrößen (für Review und Doku).

Aufruf: ``python tools/gui_screenshots.py [Zielordner]``; jede Skalierung läuft in einem
eigenen Prozess, weil Qt den Skalierungsfaktor nur beim Start übernimmt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCALES = ("1.0", "1.25", "1.5", "2.0")
# Logische Fenstergröße = Bildschirm / Skalierung, abzüglich Taskleiste und Rahmen.
SCREENS = {"1.0": (1366, 728), "1.25": (1536, 824), "1.5": (1280, 680), "2.0": (1920, 1040)}
SCREEN_NAMES = {
    "1.0": "1366x768-100",
    "1.25": "1920x1080-125",
    "1.5": "1920x1080-150",
    "2.0": "3840x2160-200",
}
NOW = datetime(2026, 10, 1, 10, 45, tzinfo=ZoneInfo("Europe/Berlin"))


def _render(scale: str, out: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))

    from icware_auftragsimport.app.demo import build_demo  # noqa: PLC0415
    from icware_auftragsimport.app.presentation import DetailTab  # noqa: PLC0415
    from icware_auftragsimport.app.workbench import Workbench  # noqa: PLC0415
    from icware_auftragsimport.gui.app import create_application  # noqa: PLC0415
    from icware_auftragsimport.gui.export_dialog import ExportDialog  # noqa: PLC0415
    from icware_auftragsimport.gui.main_window import MainWindow  # noqa: PLC0415
    from icware_auftragsimport.infrastructure.clock import FixedClock  # noqa: PLC0415

    app = create_application(["shots"])
    database, settings = build_demo(Path(tempfile.mkdtemp()), NOW)
    workbench = Workbench(database.connect(), settings, FixedClock(NOW))
    window = MainWindow(workbench, demo=True, persist=False)

    def shot(
        name: str, width: int, height: int, order: str | None = None, tab: DetailTab | None = None
    ) -> None:
        window.resize(width, height)
        window.show()
        if order:
            window.table.select_order(order)
        if tab:
            window.detail.show_tab(tab)
        for _ in range(3):
            app.processEvents()
        window.grab().save(str(out / f"{name}@{scale}.png"))

    width, height = SCREENS[scale]
    shot(f"haupt-{SCREEN_NAMES[scale]}", width, height, "order-002", DetailTab.VALIDATION)
    if scale == "1.25":
        shot("uebersicht", width, height, "order-001", DetailTab.OVERVIEW)
        shot("positionen", width, height, "order-002", DetailTab.LINES)
        shot("lieferadresse", width, height, "order-003", DetailTab.DELIVERY)
        shot("schmal", 940, 700, "order-003", DetailTab.VALIDATION)
        _settings_shots(app, window, workbench, out, scale)
        dialog = ExportDialog(workbench, window)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(out / f"export-vorher@{scale}.png"))
        dialog._run()
        app.processEvents()
        dialog.grab().save(str(out / f"export-ergebnis@{scale}.png"))
    window.close()


PRICE_LIST = (
    "Artikelnummer;Bezeichnung;Einheit;Steuerart;Verkaufspreis netto;GTIN;Aktiv\r\n"
    "MW-0710;Mineralwasser still 12 × 0,7 l;Kiste;19 %;7,20;4012345000017;ja\r\n"
    "MW-0720;Mineralwasser medium 12 × 0,7 l;Kiste;19 %;7,20;4012345000024;ja\r\n"
    "MW-1000;Mineralwasser still 6 × 1,0 l PET;Kiste;19 %;5,40;4012345000017;ja\r\n"
    "AS-1000;Apfelschorle 12 × 1,0 l;Kiste;19 %;11,40;;ja\r\n"
    "OS-1000;Orangensaft Direktsaft 6 × 1,0 l;Karton;19 %;14,80;;ja\r\n"
    "KF-1000;Kaffee Crema ganze Bohne 1 kg;Beutel;7 %;18,20;;ja\r\n"
    "MI-1035;Frischmilch 3,5 % 12 × 1,0 l;Karton;7 %;13,20;;ja\r\n"
    "SV-0500;Servietten 3-lagig 40 × 40, 500 Stk;Packung;19 %;9,95;;ja\r\n"
    "BC-0200;Becher 0,2 l Pappe, 1000 Stk;Karton;19 %;42,00;;nein\r\n"
).encode("cp1252")


def _settings_shots(app: object, window: object, workbench: object, out: Path, scale: str) -> None:
    from icware_auftragsimport.app.presentation import DetailTab  # noqa: PLC0415
    from icware_auftragsimport.gui.settings.catalog_dialogs import (  # noqa: PLC0415
        BackupDialog,
        ImportDialog,
        VersionsDialog,
    )
    from icware_auftragsimport.gui.settings.dialog import SettingsDialog  # noqa: PLC0415

    def grab(widget: object, name: str) -> None:
        widget.show()  # type: ignore[attr-defined]
        for _ in range(3):
            app.processEvents()  # type: ignore[attr-defined]
        widget.grab().save(str(out / f"{name}@{scale}.png"))  # type: ignore[attr-defined]

    settings = SettingsDialog(workbench, window)  # type: ignore[arg-type]
    settings.profiles.tabs.setCurrentIndex(1)
    settings.profiles.sender_test.setText("einkauf@nord.gasthaus.example")
    grab(settings, "einstellungen-profile")
    settings.open_catalog()
    grab(settings, "einstellungen-katalog")
    settings.close()
    wizard = ImportDialog(workbench, window)  # type: ignore[arg-type]
    wizard.load_bytes(PRICE_LIST, "preisliste-2027.csv")
    wizard.go(1)
    grab(wizard, "katalog-import-zuordnung")
    wizard.go(2)
    grab(wizard, "katalog-import-pruefung")
    wizard.close()
    grab(VersionsDialog(workbench, window), "katalog-versionen")  # type: ignore[arg-type]
    grab(BackupDialog(workbench, window), "katalog-backups")  # type: ignore[arg-type]
    workbench.switch_profile("nordlicht")  # type: ignore[attr-defined]
    window.refresh(select_first=True)  # type: ignore[attr-defined]
    window.resize(*SCREENS[scale])  # type: ignore[attr-defined]
    window.table.select_order("order-102")  # type: ignore[attr-defined]
    window.detail.show_tab(DetailTab.LINES)  # type: ignore[attr-defined]
    window.detail.lines.table.setCurrentCell(1, 0)  # type: ignore[attr-defined]
    grab(window, "positionen-zuordnung")
    workbench.switch_profile("standard")  # type: ignore[attr-defined]
    window.refresh(select_first=True)  # type: ignore[attr-defined]


def main() -> None:
    """Startet einen Prozess je Skalierung."""
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "gui" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ICW_SHOT_SCALE"):
        _render(os.environ["ICW_SHOT_SCALE"], out)
        return
    for scale in SCALES:
        env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen"),
               "QT_SCALE_FACTOR": scale, "ICW_SHOT_SCALE": scale}  # fmt: skip
        subprocess.run([sys.executable, __file__, str(out)], env=env, check=True)


if __name__ == "__main__":
    main()
