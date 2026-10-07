"""Länderkennungen und Postleitzahlregeln.

Geprüft werden nur Länder mit hinterlegter Regel; für alle anderen meldet die
Validierung ausdrücklich, dass das PLZ-Format nicht geprüft wurde.
"""

from __future__ import annotations

import re

_COUNTRY_CODE = re.compile(r"[A-Z]{2}")

COUNTRY_NAMES: dict[str, str] = {
    "DE": "Deutschland",
    "AT": "Österreich",
    "CH": "Schweiz",
    "NL": "Niederlande",
    "BE": "Belgien",
    "LU": "Luxemburg",
    "FR": "Frankreich",
    "IT": "Italien",
    "PL": "Polen",
    "DK": "Dänemark",
    "CZ": "Tschechien",
    "ES": "Spanien",
}

COUNTRY_ALIASES: dict[str, str] = {
    "deutschland": "DE",
    "germany": "DE",
    "bundesrepublik deutschland": "DE",
    "de": "DE",
    "österreich": "AT",
    "oesterreich": "AT",
    "austria": "AT",
    "at": "AT",
    "schweiz": "CH",
    "switzerland": "CH",
    "suisse": "CH",
    "ch": "CH",
    "niederlande": "NL",
    "netherlands": "NL",
    "holland": "NL",
    "nl": "NL",
    "belgien": "BE",
    "belgium": "BE",
    "be": "BE",
    "luxemburg": "LU",
    "luxembourg": "LU",
    "lu": "LU",
    "frankreich": "FR",
    "france": "FR",
    "fr": "FR",
    "italien": "IT",
    "italy": "IT",
    "italia": "IT",
    "it": "IT",
    "polen": "PL",
    "poland": "PL",
    "pl": "PL",
    "dänemark": "DK",
    "daenemark": "DK",
    "denmark": "DK",
    "dk": "DK",
    "tschechien": "CZ",
    "czech republic": "CZ",
    "cz": "CZ",
    "spanien": "ES",
    "spain": "ES",
    "es": "ES",
}

POSTAL_CODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "DE": re.compile(r"\d{5}"),
    "AT": re.compile(r"\d{4}"),
    "CH": re.compile(r"\d{4}"),
    "BE": re.compile(r"\d{4}"),
    "LU": re.compile(r"\d{4}"),
    "DK": re.compile(r"\d{4}"),
    "NL": re.compile(r"\d{4} ?[A-Z]{2}"),
    "FR": re.compile(r"\d{5}"),
    "IT": re.compile(r"\d{5}"),
    "ES": re.compile(r"\d{5}"),
    "PL": re.compile(r"\d{2}-\d{3}"),
    "CZ": re.compile(r"\d{3} ?\d{2}"),
}


def is_country_code(value: str) -> bool:
    """True für zwei Großbuchstaben nach ISO 3166-1 alpha-2 (Format, nicht Liste)."""
    return bool(_COUNTRY_CODE.fullmatch(value))


def country_from_text(text: str) -> str | None:
    """Übersetzt einen Ländernamen oder Code in einen ISO-Code, sonst ``None``."""
    return COUNTRY_ALIASES.get(" ".join(text.casefold().split()))


def country_label(code: str) -> str:
    """Deutscher Ländername oder der Code selbst, wenn unbekannt."""
    return COUNTRY_NAMES.get(code, code)


def has_postal_rule(country: str) -> bool:
    """True, wenn für das Land ein PLZ-Format hinterlegt ist."""
    return country in POSTAL_CODE_PATTERNS


def postal_code_problem(country: str, postal_code: str) -> str | None:
    """Beschreibt ein falsches PLZ-Format oder gibt ``None`` zurück."""
    pattern = POSTAL_CODE_PATTERNS.get(country)
    if pattern is None or pattern.fullmatch(postal_code.strip()):
        return None
    return f"Die PLZ „{postal_code}“ passt nicht zum Format für {country_label(country)}"
