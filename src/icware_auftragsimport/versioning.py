"""Semantische Versionen (SemVer 2.0.0) und ihre Windows-Entsprechungen.

Windows Installer vergleicht ``ProductVersion`` nur über die ersten drei Felder
(Major ≤ 255, Minor ≤ 255, Build ≤ 65535); ein viertes Feld wird beim Upgrade ignoriert.
Die Build-Nummer gehört deshalb in die Dateiversion der EXE, nie in die MSI-Version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
MSI_LIMITS = (255, 255, 65535)
WINDOWS_FIELD_MAX = 65535


class VersionError(ValueError):
    """Keine gültige Version oder außerhalb der Windows-Grenzen."""


@dataclass(frozen=True, slots=True)
class Version:
    """Version nach SemVer 2.0.0."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    metadata: str = ""

    @classmethod
    def parse(cls, text: str) -> Version:
        """``2.0.1``, ``2.1.0-rc.1`` oder ``2.1.0+build.7``."""
        match = SEMVER.fullmatch(text.strip())
        if match is None:
            raise VersionError(f"„{text}“ ist keine Version nach SemVer (z. B. 2.0.1)")
        major, minor, patch, pre, meta = match.groups()
        return cls(
            int(major), int(minor), int(patch), tuple(pre.split(".")) if pre else (), meta or ""
        )

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        return text + (f"+{self.metadata}" if self.metadata else "")

    def _key(self) -> tuple[object, ...]:
        if not self.prerelease:
            return (self.major, self.minor, self.patch, 1, ())
        parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.prerelease)
        return (self.major, self.minor, self.patch, 0, parts)

    def __lt__(self, other: Version) -> bool:
        return self._key() < other._key()

    def __le__(self, other: Version) -> bool:
        return self._key() <= other._key()

    @property
    def is_release(self) -> bool:
        """Ohne Vorabkennung (``-rc.1``)."""
        return not self.prerelease

    def bump(self, part: str) -> Version:
        """Nächste Version: ``major``, ``minor`` oder ``patch``."""
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "patch":
            return Version(self.major, self.minor, self.patch + (0 if self.prerelease else 1))
        raise VersionError(f"Unbekannter Versionsteil „{part}“ (major, minor oder patch)")

    def msi_version(self) -> str:
        """Version für Windows Installer, etwa ``2.0.1``."""
        fields = (self.major, self.minor, self.patch)
        for value, limit, name in zip(fields, MSI_LIMITS, ("Major", "Minor", "Patch"), strict=True):
            if value > limit:
                raise VersionError(
                    f"{name} {value} überschreitet die Windows-Installer-Grenze {limit}"
                )
        return ".".join(str(v) for v in fields)

    def file_version(self, build: int) -> tuple[int, int, int, int]:
        """Vierteilige Dateiversion der EXE, etwa ``(2, 0, 1, 57)``."""
        fields = (self.major, self.minor, self.patch, build)
        if any(not 0 <= v <= WINDOWS_FIELD_MAX for v in fields):
            raise VersionError(f"Dateiversion {fields} außerhalb 0–{WINDOWS_FIELD_MAX}")
        return fields
