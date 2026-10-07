"""Zahlen im deutschen Format mit ausdrücklicher Mehrdeutigkeit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_ALLOWED = re.compile(r"\d[\d.,]*")
_STRIP = re.compile(r"[\s'’\u2009]|€|eur(?:o)?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedNumber:
    """Zahl mit Hinweis, ob die Schreibweise zwei Lesarten zulässt (``1.234``)."""

    value: Decimal
    ambiguous: bool


def _valid_groups(integer_part: str, separator: str) -> bool:
    groups = integer_part.split(separator)
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def _number(text: str, ambiguous: bool) -> ParsedNumber | None:
    try:
        return ParsedNumber(Decimal(text), ambiguous)
    except InvalidOperation:
        return None


def _mixed(cleaned: str) -> ParsedNumber | None:
    decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
    thousands = "." if decimal_sep == "," else ","
    integer_part, fraction = cleaned.rsplit(decimal_sep, 1)
    if not _valid_groups(integer_part, thousands):
        return None
    return _number(f"{integer_part.replace(thousands, '')}.{fraction}", thousands == ",")


def _dotted(cleaned: str) -> ParsedNumber | None:
    parts = cleaned.split(".")
    if len(parts) == 2 and len(parts[1]) != 3:
        return _number(cleaned, True)
    if not _valid_groups(cleaned, "."):
        return None
    return _number(cleaned.replace(".", ""), True)


def parse_decimal_de(text: str) -> ParsedNumber | None:
    """Liest ``1.234,56``, ``12,5``, ``1.000`` und ``12.50``.

    Ein Punkt mit genau drei Folgeziffern gilt als Tausendertrennzeichen (deutsch), ein
    Punkt mit ein oder zwei Nachkommastellen als Dezimalpunkt; beides wird als mehrdeutig
    markiert, damit Mengen und Preise in dieser Schreibweise geprüft werden.
    """
    cleaned = _STRIP.sub("", text.strip())
    if not _ALLOWED.fullmatch(cleaned) or cleaned.endswith((".", ",")):
        return None
    if "," in cleaned and "." in cleaned:
        return _mixed(cleaned)
    if "," in cleaned:
        return None if cleaned.count(",") > 1 else _number(cleaned.replace(",", "."), False)
    if "." in cleaned:
        return _dotted(cleaned)
    return _number(cleaned, False)
