# PyInstaller-Spezifikation für IC-Ware Auftrags-Import.
# Nur über tools/release/build.py aufrufen (setzt PYTHONHASHSEED, SOURCE_DATE_EPOCH, Versionsressource).
#
# Entscheidungen:
# - onedir statt onefile: kein Entpacken in %TEMP% bei jedem Start (schneller, weniger Fehlalarme von
#   Virenscannern) und Qt/PySide6-Bibliotheken bleiben austauschbar (LGPL-Pflicht).
# - kein UPX: gepackte Dateien lösen häufiger Fehlalarme aus und erschweren die Signaturprüfung.
# - keine Konsole; PyInstallers eigene Fehleranzeige ist aus, Fehler zeigt der Fehlerdialog der Anwendung.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821
BUILD = ROOT / "build"
ICON = ROOT / "packaging" / "windows" / "AuftragsImport.ico"
VERSION_FILE = BUILD / "version_info.txt"

datas = collect_data_files("icware_auftragsimport", includes=["**/*.json"])

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "pyinstaller" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=["icware_auftragsimport._build_info", "keyring.backends.Windows"],
    # setuptools/_distutils_hack: Build-Werkzeuge, kämen sonst über PyInstallers Laufzeit-Hook ins
    # Produkt (~150 Module, u. a. MSVC-Compiler-Anbindung). tools/release/audit.py prüft das.
    excludes=["tkinter", "unittest", "pydoc", "lib2to3", "PySide6.QtWebEngineCore", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtMultimedia", "PySide6.QtNetwork",
              "setuptools", "pkg_resources", "_distutils_hack", "distutils"],
    optimize=0,
    noarchive=False,
)

# Qt-Übersetzungen: nur Deutsch (Standardknöpfe, Dateidialoge).
a.datas = [entry for entry in a.datas
           if not (entry[0].endswith(".qm") and "translations" in entry[0].replace("\\", "/") and "_de" not in entry[0])]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AuftragsImport",
    console=False,
    disable_windowed_traceback=True,
    icon=str(ICON) if ICON.exists() else None,
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
    upx=False,
    strip=False,
    uac_admin=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AuftragsImport")  # noqa: F821
