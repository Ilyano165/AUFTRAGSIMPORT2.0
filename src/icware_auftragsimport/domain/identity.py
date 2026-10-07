"""Normalisierte Kennungen für Duplikatabgleich und Speicherung."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^0-9A-Z]")


def normalize_reference(value: str) -> str:
    """Bestellnummern ohne Groß-/Kleinschreibung, Leerzeichen und Trennzeichen."""
    return _NON_ALNUM.sub("", value.upper())


def customer_key(customer_number: str | None, sender_email: str) -> str:
    """Kundenkennung für den Duplikatabgleich: Kundennummer, sonst Absenderdomain."""
    if customer_number and normalize_reference(customer_number):
        return f"kd:{normalize_reference(customer_number)}"
    domain = sender_email.rpartition("@")[2].strip().casefold()
    return f"dom:{domain}" if domain else ""
