"""Vorbereitung für Updates: Manifest-Schema, Versionsvergleich, Richtlinie.

Bewusst ohne Netzwerkzugriff, ohne Download und ohne Ausführung. Ein späterer Abruf muss
signierte Metadaten mit fest eingebautem Schlüssel prüfen (geplant: The Update Framework über
``tufup``), die MSI-Prüfsumme und die Authenticode-Signatur kontrollieren und die Installation
an Windows Installer übergeben. Bis dahin verteilt die IT Updates als MSI (Intune, GPO, winget).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from ..versioning import Version, VersionError

MANIFEST_FORMAT = "icware-update-manifest"
ALLOWED_HOSTS = ("ic-ware.eu", "downloads.ic-ware.eu")
MAX_MANIFEST_BYTES = 32 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_URL = re.compile(r"https://([A-Za-z0-9.-]+)(/[^\s]*)?")


class UpdateState(StrEnum):
    """Ergebnis des Versionsvergleichs."""

    CURRENT = "current"
    AVAILABLE = "available"
    REQUIRED = "required"


class ManifestError(ValueError):
    """Manifest ungültig; nie wird ein ungültiges Manifest teilweise verwendet."""


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    """Beschreibung der neuesten Version eines Kanals (vor der Verwendung signaturgeprüft)."""

    channel: str
    version: Version
    released: date
    minimum_version: Version | None
    notes_url: str
    download_page: str
    msi_name: str
    msi_sha256: str
    msi_size: int


def _url(value: object, field: str) -> str:
    match = _URL.fullmatch(value) if isinstance(value, str) else None
    if match is None or match.group(1).lower() not in ALLOWED_HOSTS:
        raise ManifestError(f"{field}: nur https-Adressen von {', '.join(ALLOWED_HOSTS)}")
    return str(value)


def parse_manifest(data: bytes) -> UpdateManifest:
    """Liest ein Manifest streng; jede Abweichung ist ein Fehler."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest zu groß")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ManifestError("Manifest ist kein gültiges JSON") from exc
    if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
        raise ManifestError("Unbekanntes Manifestformat")
    msi = raw.get("msi")
    if not isinstance(msi, dict):
        raise ManifestError("msi: Objekt erwartet")
    try:
        version = Version.parse(str(raw["version"]))
        minimum = Version.parse(str(raw["minimum_version"])) if raw.get("minimum_version") else None
        released = date.fromisoformat(str(raw["released"]))
    except (KeyError, VersionError, ValueError) as exc:
        raise ManifestError(f"Version oder Datum ungültig ({exc})") from exc
    sha = str(msi.get("sha256", "")).lower()
    name = str(msi.get("name", ""))
    size = msi.get("size")
    if not _SHA256.fullmatch(sha):
        raise ManifestError("msi.sha256: 64 Hexadezimalzeichen erwartet")
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.msi", name):
        raise ManifestError("msi.name: Dateiname mit Endung .msi erwartet")
    if not isinstance(size, int) or not 0 < size < 2**31:
        raise ManifestError("msi.size: positive Bytezahl erwartet")
    if raw.get("channel") not in ("stable", "beta"):
        raise ManifestError("channel: stable oder beta")
    return UpdateManifest(
        channel=str(raw["channel"]),
        version=version,
        released=released,
        minimum_version=minimum,
        notes_url=_url(raw.get("notes_url"), "notes_url"),
        download_page=_url(raw.get("download_page"), "download_page"),
        msi_name=name,
        msi_sha256=sha,
        msi_size=size,
    )


def evaluate(manifest: UpdateManifest, installed: Version, channel: str) -> UpdateState:
    """Vergleicht die installierte Version; Vorabversionen nur im Kanal „beta“."""
    if manifest.channel != channel or (not manifest.version.is_release and channel != "beta"):
        return UpdateState.CURRENT
    if manifest.minimum_version is not None and installed < manifest.minimum_version:
        return UpdateState.REQUIRED
    return UpdateState.AVAILABLE if installed < manifest.version else UpdateState.CURRENT
