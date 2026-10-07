"""Laden, Speichern, Sichern, Importieren und Exportieren der Einstellungen."""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.errors import ConfigError, UserMessage
from ..domain.ports import Clock
from ..infrastructure.atomic import atomic_write_text
from .schema import Settings, parse_settings, settings_to_dict

MAX_BACKUPS = 20
BACKUP_PATTERN = "settings-*.json"


class ConfigStore:
    """Einstellungsdatei mit automatischer Sicherung vor jeder Änderung."""

    def __init__(self, config_file: Path, backup_dir: Path, clock: Clock) -> None:
        self._file = config_file
        self._backup_dir = backup_dir
        self._clock = clock

    def load(self) -> Settings:
        """Liest die Einstellungen; ohne Datei gelten die Standardwerte."""
        if not self._file.exists():
            return Settings()
        return self._read(self._file)

    def save(self, settings: Settings) -> None:
        """Prüft, sichert die bisherige Datei und schreibt atomar."""
        data = settings_to_dict(settings)
        parse_settings(data)
        self.backup()
        atomic_write_text(self._file, _dump(data))

    def backup(self) -> Path | None:
        """Legt eine Sicherung der aktuellen Datei an und räumt alte Sicherungen auf."""
        if not self._file.exists():
            return None
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._clock.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self._backup_dir / f"settings-{stamp}.json"
        atomic_write_text(target, self._file.read_text(encoding="utf-8"))
        for old in self.list_backups()[MAX_BACKUPS:]:
            old.unlink(missing_ok=True)
        return target

    def list_backups(self) -> list[Path]:
        """Sicherungen, neueste zuerst."""
        if not self._backup_dir.exists():
            return []
        return sorted(self._backup_dir.glob(BACKUP_PATTERN), reverse=True)

    def restore(self, backup: Path) -> Settings:
        """Stellt eine Sicherung wieder her, nachdem sie geprüft wurde."""
        settings = self._read(backup)
        self.save(settings)
        return settings

    def export_to(self, target: Path, settings: Settings) -> None:
        """Schreibt die Einstellungen für die Weitergabe an andere Arbeitsplätze."""
        atomic_write_text(target, _dump(settings_to_dict(settings)))

    def import_from(self, source: Path) -> Settings:
        """Übernimmt eine exportierte Datei; bei Fehlern bleibt alles unverändert."""
        settings = self._read(source)
        self.save(settings)
        return settings

    def _read(self, path: Path) -> Settings:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(
                "CONFIG_UNREADABLE",
                UserMessage(
                    what=f"Die Einstellungsdatei „{path.name}“ konnte nicht gelesen werden",
                    why=f"Das Betriebssystem meldet: {exc.strerror or type(exc).__name__}",
                    unchanged="Es wurden keine Einstellungen verändert",
                    action="Bitte Zugriffsrechte und Speicherort prüfen",
                ),
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "CONFIG_NOT_JSON",
                UserMessage(
                    what=f"Die Einstellungsdatei „{path.name}“ ist beschädigt",
                    why=f"Zeile {exc.lineno}, Spalte {exc.colno}: {exc.msg}",
                    unchanged="Es wurden keine Einstellungen verändert",
                    action="Bitte eine Sicherung wiederherstellen oder die Datei korrigieren",
                ),
            ) from exc
        return parse_settings(data)


def _dump(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
