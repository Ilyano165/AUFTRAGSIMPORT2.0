"""Entfernt sensible Daten aus Texten für Protokoll und Diagnosebericht."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

REDACTED = "<entfernt>"

_Replacement = str | Callable[[re.Match[str]], str]

_RULES: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    (
        re.compile(
            r"(?i)\b(password|passwort|kennwort|pwd|token|secret|api[_-]?key|authorization)"
            r"(\s*[:=]\s*)(?:Bearer\s+)?\S+"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), f"Bearer {REDACTED}"),
    (
        re.compile(
            r"(?i)\b(konto(?:nummer|nr\.?)?|kto\.?(?:-?nr\.?)?|blz|bic|bankleitzahl|account)"
            r"(\s*[:#.]?\s*)[A-Z0-9][A-Z0-9 ]{3,33}[A-Z0-9]\b"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}<Konto>",
    ),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"), "<IBAN>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<E-Mail>"),
    (re.compile(r"(?<![\w.])(?:\d[ -]?){13,19}(?![\w.])"), "<Nummer>"),
    (re.compile(r"(?<![\w+])(?:\+|00)\d[\d /-]{7,}\d"), "<Telefon>"),
    (re.compile(r"(?<!\w)0\d{2,5} ?[/-] ?\d{3,}(?:[ -]?\d+)*"), "<Telefon>"),
    (re.compile(r"(?<![\w+])(?:\(0\d{2,5}\) ?|0\d{2,5}[ /-])\d{3,}(?:[ -]\d{2,})*"), "<Telefon>"),
    (
        re.compile(
            r"(?i)\b[\wäöüß.-]{2,40}(?:straße|strasse|str\.|weg|gasse|platz|allee|ring|damm"
            r"|ufer|chaussee|steig|pfad|markt|hof)\s?\d{1,4}\s?[a-z]?(?:\s?[-/]\s?\d{1,4}[a-z]?)?\b"
        ),
        "<Adresse>",
    ),
    (re.compile(r"(?i)\bpostfach\s+\d[\d ]{1,10}"), "<Adresse>"),
    (re.compile(r"(?<![\w-])(?:D-)?\d{5}\s+[A-ZÄÖÜ][\wäöüß.-]{1,40}"), "<PLZ Ort>"),
)
_SECRETS: set[str] = set()
MIN_SECRET_LENGTH = 4

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwort",
        "token",
        "secret",
        "iban",
        "email",
        "phone",
        "username",
        "sender",
        "name",
        "street",
        "company",
        "city",
        "postal_code",
        "zip",
        "subject",
        "body",
        "text",
        "address",
        "account",
        "bank_account",
    }
)


def register_secret(value: str | None) -> None:
    """Merkt ein Geheimnis vor; jedes exakte Vorkommen wird künftig entfernt."""
    if value and len(value) >= MIN_SECRET_LENGTH:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """Ersetzt Geheimnisse, Zugangsdaten, IBAN/Konten, Mail, Telefon, Anschriften."""
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_mapping(data: Mapping[str, object]) -> dict[str, object]:
    """Kopie eines Dictionarys, in der sensible Schlüssel und Texte bereinigt sind."""
    result: dict[str, object] = {}
    for key, value in data.items():
        if key.casefold() in SENSITIVE_KEYS and value not in ("", None):
            result[key] = REDACTED
        elif isinstance(value, Mapping):
            result[key] = redact_mapping(value)
        elif isinstance(value, str):
            result[key] = redact(value)
        else:
            result[key] = value
    return result


def anonymize_paths(text: str, home: str | None = None, user: str | None = None) -> str:
    """Ersetzt Benutzerordner und Benutzernamen in Pfaden (für Protokolle und Fehlerberichte)."""
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    home_dir = home if home is not None else str(Path.home())
    name = (
        user if user is not None else (os.environ.get("USERNAME") or os.environ.get("USER") or "")
    )
    if home_dir and len(home_dir) > 3:
        for variant in {home_dir, home_dir.replace("\\", "/"), home_dir.replace("/", "\\")}:
            text = text.replace(variant, "%USERPROFILE%")
    if name and len(name) > 2:
        for sep in ("\\", "/"):
            text = text.replace(f"{sep}{name}{sep}", f"{sep}<benutzer>{sep}")
    return text
