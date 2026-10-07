"""Build-Informationen: Version, Build, Commit, Build-Zeitpunkt.

Der Build-Prozess erzeugt ``_build_info.py`` (nicht im Quellcode-Archiv eingecheckt). Der
Zeitpunkt ist der Commit-Zeitpunkt (``SOURCE_DATE_EPOCH``), damit derselbe Commit immer
dieselben Build-Informationen ergibt. Ohne erzeugte Datei gilt der Entwicklungsstand.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import UTC, datetime

from . import __version__

GENERATED_MODULE = "icware_auftragsimport._build_info"
DEVELOPMENT_BUILD = "lokal"
UNVERSIONED = "unversioniert"


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """Angaben zum laufenden Build."""

    version: str
    build: str
    commit: str
    timestamp: datetime | None
    dirty: bool = False

    @property
    def short_commit(self) -> str:
        """Gekürzter Commit (10 Zeichen)."""
        short = self.commit if self.commit == UNVERSIONED else self.commit[:10]
        return short + (" (geändert)" if self.dirty else "")

    @property
    def timestamp_text(self) -> str:
        """Build-Zeitpunkt in UTC."""
        return f"{self.timestamp:%Y-%m-%d %H:%M} UTC" if self.timestamp else "–"

    @property
    def is_release(self) -> bool:
        """Aus der Build-Pipeline mit sauberem Commit."""
        return self.build != DEVELOPMENT_BUILD and self.commit != UNVERSIONED and not self.dirty

    def describe(self) -> str:
        """Einzeilig, etwa „2.0.1 · Build 57 · a1b2c3d4e5 · 2026-10-01 08:45 UTC“."""
        return f"{self.version} · Build {self.build} · {self.short_commit} · {self.timestamp_text}"

    def as_dict(self) -> dict[str, str]:
        """Für Diagnose und Absturzberichte."""
        return {"version": self.version, "build": self.build, "commit": self.commit,
                "timestamp": self.timestamp_text, "dirty": str(self.dirty).lower()}  # fmt: skip


def current() -> BuildInfo:
    """Build-Informationen des laufenden Programms."""
    try:
        generated = importlib.import_module(GENERATED_MODULE)
    except ImportError:
        return BuildInfo(__version__, DEVELOPMENT_BUILD, UNVERSIONED, None)
    stamp = getattr(generated, "TIMESTAMP", None)
    return BuildInfo(
        version=str(getattr(generated, "VERSION", __version__)),
        build=str(getattr(generated, "BUILD", DEVELOPMENT_BUILD)),
        commit=str(getattr(generated, "COMMIT", UNVERSIONED)),
        timestamp=datetime.fromtimestamp(int(stamp), UTC) if isinstance(stamp, int) else None,
        dirty=bool(getattr(generated, "DIRTY", False)),
    )


def render_module(info: BuildInfo) -> str:
    """Inhalt von ``_build_info.py``; deterministisch für gleiche Eingaben."""
    stamp = int(info.timestamp.timestamp()) if info.timestamp else None
    return (
        '"""Vom Build-Prozess erzeugt. Nicht bearbeiten, nicht einchecken."""\n\n'
        f"VERSION = {info.version!r}\nBUILD = {info.build!r}\nCOMMIT = {info.commit!r}\n"
        f"TIMESTAMP = {stamp!r}\nDIRTY = {info.dirty!r}\n"
    )
