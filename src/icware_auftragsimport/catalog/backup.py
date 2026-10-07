"""Katalog-Backups als JSON-Dateien je Profil, mit Prüfsumme und Aufbewahrungsregel."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..domain.models import Article
from ..infrastructure.atomic import atomic_create_in
from ..security.fs import safe_filename
from .export import catalog_document
from .table import JSON_FORMAT, RawTable, read_table

KEEP_PER_PROFILE = 30
MAX_NAME_ATTEMPTS = 50
SUFFIX = ".json"


class BackupError(ValueError):
    """Backup fehlt, ist beschädigt oder gehört nicht zum Profil."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """Ein vorhandenes Backup."""

    path: Path
    created: str
    catalog_version: int | None
    article_count: int
    reason: str
    intact: bool


def _digest(articles: list[dict[str, object]]) -> str:
    canonical = json.dumps(articles, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CatalogBackups:
    """Backup-Ordner, je Profil ein Unterordner."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def folder(self, profile_id: str) -> Path:
        """Ordner eines Profils (wird bei Bedarf angelegt)."""
        folder = self.directory / safe_filename(profile_id, fallback="profil")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder

    def create(
        self,
        articles: Sequence[Article],
        *,
        profile_id: str,
        profile_name: str,
        version: int | None,
        now: datetime,
        reason: str,
    ) -> Path:
        """Schreibt ein Backup atomar (0600, ohne Symlinks zu folgen) und räumt alte auf."""
        document = catalog_document(
            articles,
            profile_id=profile_id,
            profile_name=profile_name,
            version=version,
            created=now,
            source=reason,
        )
        document["sha256"] = _digest(document["articles"])  # type: ignore[arg-type]
        stamp = now.strftime("%Y%m%d-%H%M%S-%f")
        data = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
        base = f"katalog-{profile_id}-v{version or 0:04d}-{stamp}"
        path: Path | None = None
        for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
            name = f"{base}{SUFFIX}" if attempt == 1 else f"{base}-{attempt}{SUFFIX}"
            try:
                path = atomic_create_in(self.folder(profile_id), name, data)
                break
            except FileExistsError:
                continue
        if path is None:
            raise OSError(17, "Kein freier Dateiname für das Backup")
        self._prune(profile_id)
        return path

    def _prune(self, profile_id: str) -> None:
        for old in self._files(profile_id)[KEEP_PER_PROFILE:]:
            old.unlink(missing_ok=True)

    def _files(self, profile_id: str) -> list[Path]:
        files = list(self.folder(profile_id).glob(f"katalog-*{SUFFIX}"))
        return sorted(files, key=lambda f: (f.stat().st_mtime_ns, f.name), reverse=True)

    def entries(self, profile_id: str) -> list[BackupInfo]:
        """Backups eines Profils, neueste zuerst."""
        infos = []
        for path in self._files(profile_id):
            try:
                document = self._read(path, profile_id)
                articles = document["articles"]
                count = len(articles) if isinstance(articles, list) else 0
                raw_version = document.get("catalog_version")
                version = raw_version if isinstance(raw_version, int) else None
                created, reason = str(document.get("created", "")), str(document.get("source", ""))
                infos.append(BackupInfo(path, created, version, count, reason, True))
            except BackupError:
                infos.append(BackupInfo(path, "", None, 0, "beschädigt", False))
        return infos

    def _read(self, path: Path, profile_id: str) -> dict[str, object]:
        folder = self.folder(profile_id).resolve()
        resolved = path.resolve()
        if resolved.parent != folder or path.is_symlink():
            raise BackupError("Das Backup liegt nicht im Backup-Ordner dieses Profils")
        try:
            document = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"Backup nicht lesbar: {path.name}") from exc
        if not isinstance(document, dict) or document.get("format") != JSON_FORMAT:
            raise BackupError("Keine Katalogsicherung von IC-Ware Auftrags-Import")
        if document.get("profile_id") != profile_id:
            raise BackupError("Das Backup gehört zu einem anderen Profil")
        articles = document.get("articles")
        if not isinstance(articles, list) or document.get("sha256") != _digest(articles):
            raise BackupError(
                "Prüfsumme stimmt nicht; das Backup wurde verändert oder ist beschädigt"
            )
        return document

    def load(self, path: Path, profile_id: str) -> RawTable:
        """Geprüftes Backup als Tabelle (wird wie ein JSON-Import weiterverarbeitet)."""
        self._read(path, profile_id)
        return read_table(path.read_bytes(), path.name)
