"""Ablageorte nach Windows-Konventionen. Werden einmal bestimmt und dann weitergereicht.

=========================================  ===========================================  ==========
Ort                                        Inhalt                                       schreibt
=========================================  ===========================================  ==========
Program Files\\IC-Ware\\Auftrags-Import     Programmdateien                              Installer
%PROGRAMDATA%\\IC-Ware\\Auftrags-Import     Richtlinie der IT (``policy.json``)          IT
%APPDATA%\\IC-Ware\\Auftrags-Import         Einstellungen und ihre Sicherungen           Benutzer
%LOCALAPPDATA%\\IC-Ware\\Auftrags-Import    Datenbank, Katalog-Backups, Protokolle,      Benutzer
                                           Fehlerberichte, Diagnose
=========================================  ===========================================  ==========

Die Datenbank liegt bewusst nicht im wandernden Profil (Roaming): Servergespeicherte Profile
synchronisieren beim Abmelden und vertragen keine offene SQLite-Datei. Passwörter liegen
ausschließlich in der Windows-Anmeldeinformationsverwaltung, nie in einer Datei.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..branding import DATA_DIR_PRODUCT, DATA_DIR_VENDOR

DATABASE_NAME = "auftragsimport.db"
SQLITE_COMPANIONS = ("", "-wal", "-shm")
LEGACY_DIRECTORIES = ("katalog-backups", "diagnostics")


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Pfade der Anwendung; ``root`` ist der lokale Datenordner."""

    root: Path
    config_root: Path | None = None
    machine_root: Path | None = None

    @classmethod
    def default(
        cls, environ: Mapping[str, str] | None = None, platform: str | None = None
    ) -> AppPaths:
        """Standardorte für Windows; unter Linux die XDG-Entsprechungen (Entwicklung)."""
        env = os.environ if environ is None else environ
        system = sys.platform if platform is None else platform
        home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
        if system.startswith("win"):
            local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
            roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
            machine = Path(env.get("PROGRAMDATA") or "C:/ProgramData")
        else:
            local = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
            roaming = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
            machine = Path((env.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")[0])
        product = Path(DATA_DIR_VENDOR) / DATA_DIR_PRODUCT
        return cls(
            root=local / product, config_root=roaming / product, machine_root=machine / product
        )

    @property
    def config_dir(self) -> Path:
        """Einstellungen (wandern mit dem Windows-Profil)."""
        return self.config_root or self.root

    @property
    def config_file(self) -> Path:
        """Einstellungen ohne Geheimnisse."""
        return self.config_dir / "settings.json"

    @property
    def backup_dir(self) -> Path:
        """Sicherungen der Einstellungen."""
        return self.config_dir / "backups"

    @property
    def database_file(self) -> Path:
        """Lokale Datenbank mit Aufträgen, Journal und Katalogen."""
        return self.root / DATABASE_NAME

    @property
    def catalog_backup_dir(self) -> Path:
        """Katalog-Backups je Profil."""
        return self.root / "katalog-backups"

    @property
    def log_dir(self) -> Path:
        """Protokolldateien mit Rotation."""
        return self.root / "logs"

    @property
    def crash_dir(self) -> Path:
        """Fehlerberichte (ohne Geheimnisse, ohne Variableninhalte)."""
        return self.root / "fehlerberichte"

    @property
    def lock_file(self) -> Path:
        """Sperrdatei gegen parallele Instanzen."""
        return self.root / "instance.lock"

    @property
    def diagnostics_dir(self) -> Path:
        """Support-Diagnoseberichte."""
        return self.root / "diagnostics"

    @property
    def policy_file(self) -> Path:
        """Richtlinie der IT; Benutzer lesen sie nur."""
        return (self.machine_root or self.root) / "policy.json"

    def ensure(self) -> None:
        """Legt die Benutzerordner an; der Richtlinienordner gehört dem Administrator."""
        for directory in (self.root, self.config_dir, self.backup_dir, self.log_dir, self.crash_dir,
                          self.diagnostics_dir):  # fmt: skip
            directory.mkdir(parents=True, exist_ok=True)


def migrate_legacy_layout(paths: AppPaths) -> list[str]:
    """Verschiebt Daten aus der früheren Ablage (alles im Roaming-Profil) in den lokalen Ordner.

    Läuft nur, wenn am neuen Ort noch keine Datenbank liegt; vorhandene Ziele werden nie
    überschrieben. Gibt die verschobenen Einträge zurück (für das Protokoll).
    """
    old, new = paths.config_dir, paths.root
    if old == new or not (old / DATABASE_NAME).exists() or paths.database_file.exists():
        return []
    new.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for suffix in SQLITE_COMPANIONS:
        source = old / f"{DATABASE_NAME}{suffix}"
        if source.exists():
            shutil.move(str(source), str(new / source.name))
            moved.append(source.name)
    for name in LEGACY_DIRECTORIES:
        source, target = old / name, new / name
        if source.is_dir() and not target.exists():
            shutil.move(str(source), str(target))
            moved.append(name)
    return moved
