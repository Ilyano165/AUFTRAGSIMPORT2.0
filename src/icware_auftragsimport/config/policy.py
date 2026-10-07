"""Richtlinie der IT in ``%PROGRAMDATA%``: nur lesen, nie Geheimnisse.

Fehlt die Datei, gelten sichere Standardwerte. Ist sie fehlerhaft, startet die Anwendung
trotzdem mit den Standardwerten und protokolliert die Probleme; eine kaputte Richtlinie darf
die tägliche Arbeit nicht blockieren.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..branding import SUPPORT_CONTACT

MAX_POLICY_BYTES = 64 * 1024
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
CHANNELS = ("stable", "beta")
_FORBIDDEN_KEY = re.compile(r"pass|secret|token|key|credential", re.IGNORECASE)
_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[^\s]*)?")


@dataclass(frozen=True, slots=True)
class UpdatePolicy:
    """Vorbereitung für spätere Updates; standardmäßig aus (Verteilung über die IT)."""

    check_enabled: bool = False
    channel: str = "stable"
    manifest_url: str = ""


@dataclass(frozen=True, slots=True)
class MachinePolicy:
    """Rechnerweite Vorgaben."""

    support_contact: str = SUPPORT_CONTACT
    log_level: str | None = None
    updates: UpdatePolicy = field(default_factory=UpdatePolicy)


def _secret_keys(value: object, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            where = f"{path}.{key}" if path else str(key)
            if _FORBIDDEN_KEY.search(str(key)):
                found.append(where)
            found += _secret_keys(inner, where)
    return found


def parse_policy(data: object) -> tuple[MachinePolicy, list[str]]:
    """Liest eine Richtlinie; unbekannte oder ungültige Einträge werden gemeldet und ignoriert."""
    issues: list[str] = []
    if not isinstance(data, dict):
        return MachinePolicy(), ["Die Richtlinie muss ein JSON-Objekt sein"]
    issues += [f"{key}: Geheimnisse gehören nicht in die Richtlinie" for key in _secret_keys(data)]
    if issues:
        return MachinePolicy(), issues
    issues += [
        f"{key}: unbekannter Eintrag"
        for key in data
        if key not in ("support_contact", "log_level", "updates")
    ]
    contact = data.get("support_contact", SUPPORT_CONTACT)
    if not isinstance(contact, str) or not contact.strip() or len(contact) > 200:
        issues.append("support_contact: Text mit höchstens 200 Zeichen erwartet")
        contact = SUPPORT_CONTACT
    level = data.get("log_level")
    if level is not None and level not in LOG_LEVELS:
        issues.append(f"log_level: einer von {', '.join(LOG_LEVELS)}")
        level = None
    updates = UpdatePolicy()
    raw = data.get("updates", {})
    if not isinstance(raw, dict):
        issues.append("updates: Objekt erwartet")
    else:
        issues += [
            f"updates.{key}: unbekannter Eintrag"
            for key in raw
            if key not in ("check_enabled", "channel", "manifest_url")
        ]
        enabled = raw.get("check_enabled", False)
        channel = raw.get("channel", "stable")
        url = raw.get("manifest_url", "")
        if not isinstance(enabled, bool):
            issues.append("updates.check_enabled: true oder false")
            enabled = False
        if channel not in CHANNELS:
            issues.append(f"updates.channel: einer von {', '.join(CHANNELS)}")
            channel = "stable"
        if url and (not isinstance(url, str) or not _URL.fullmatch(url)):
            issues.append("updates.manifest_url: nur https-Adressen")
            url = ""
        updates = UpdatePolicy(enabled and bool(url), channel, url)
    return MachinePolicy(contact.strip(), level, updates), issues


def load_policy(path: Path) -> tuple[MachinePolicy, list[str]]:
    """Richtlinie vom Datenträger; fehlende Datei ergibt die Standardwerte ohne Befund."""
    try:
        if not path.is_file():
            return MachinePolicy(), []
        if path.stat().st_size > MAX_POLICY_BYTES:
            return MachinePolicy(), [f"{path.name}: größer als {MAX_POLICY_BYTES // 1024} KB"]
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return MachinePolicy(), [f"{path.name}: nicht lesbar ({type(exc).__name__})"]
    return parse_policy(data)
