"""Kundendaten: Kundennummer und Umsatzsteuer-Identifikationsnummer."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.provenance import Confidence, Field
from .common import Consumed, ParseContext, evidence
from .text import Line

_CUSTOMER_NUMBER = re.compile(
    r"^[-*•\s]*(?:ihre\s+|unsere\s+)?(?:kunden\s*-?\s*(?:nummer|nr\.?)|kd\.?\s*-?\s*nr\.?"
    r"|kundennr\.?|debitor(?:en)?\s*-?\s*(?:nummer|nr\.?)?|customer\s*(?:no\.?|number|id))"
    r"\s*[:#.]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9\-/.]{0,19})",
    re.IGNORECASE,
)
_VAT_LABEL = re.compile(
    r"(?:ust\.?\s*-?\s*id(?:ent)?\.?\s*-?\s*(?:nr\.?)?|umsatzsteuer-?id(?:entifikationsnummer)?"
    r"|vat\s*(?:id|no\.?|number|reg\.?\s*no\.?)?|uid\s*-?\s*(?:nr\.?)?)\s*[:.]?\s*"
    r"(?P<value>[A-Z]{2,3}[ -]?[0-9A-Z][0-9A-Z .-]{1,16})",
    re.IGNORECASE,
)
_VAT_UNLABELED = re.compile(r"\b(?P<value>DE\d{9}|ATU\d{8})\b")
_VAT_FORMATS = (
    re.compile(r"DE\d{9}"),
    re.compile(r"ATU\d{8}"),
    re.compile(r"CHE\d{9}(?:MWST|TVA|IVA)?"),
    re.compile(r"NL\d{9}B\d{2}"),
    re.compile(r"BE[01]\d{9}"),
    re.compile(r"LU\d{8}"),
    re.compile(r"FR[0-9A-Z]{2}\d{9}"),
    re.compile(r"IT\d{11}"),
    re.compile(r"PL\d{10}"),
    re.compile(r"DK\d{8}"),
)
_HAS_DIGIT = re.compile(r"\d")


def normalize_vat_id(value: str) -> str:
    """USt-IdNr. ohne Leerzeichen, Punkte und Bindestriche, in Großbuchstaben."""
    return re.sub(r"[\s.-]", "", value.upper())


def is_valid_vat_format(value: str) -> bool:
    """True, wenn das Format einer bekannten Länderregel entspricht."""
    return any(pattern.fullmatch(value) for pattern in _VAT_FORMATS)


@dataclass(frozen=True, slots=True)
class CustomerResult:
    """Kundennummer und USt-IdNr."""

    customer_number: Field[str]
    vat_id: Field[str]


class CustomerParser:
    """Liest Kundennummer (nur beschriftet) und USt-IdNr. (beschriftet oder DE/AT-Format)."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> CustomerResult:
        """Signaturzeilen werden für die USt-IdNr. mitgelesen, Kundennummern nicht."""
        number: Field[str] = Field.unknown()
        for line in ctx.body():
            match = _CUSTOMER_NUMBER.match(line.text)
            value = match["value"].rstrip("./") if match else ""
            if value and _HAS_DIGIT.search(value):
                number = Field.found(
                    value,
                    evidence("labeled", "Beschriftete Kundennummer", Confidence.CERTAIN, line),
                )
                consumed.add(line)
                break
        return CustomerResult(number, self._vat(ctx, consumed))

    @staticmethod
    def _vat(ctx: ParseContext, consumed: Consumed) -> Field[str]:
        lines = [line for line in ctx.content() if line.is_content]
        for line in lines:
            if match := _VAT_LABEL.search(line.text):
                return _vat_field(
                    normalize_vat_id(match["value"]), line, labeled=True, used=consumed
                )
        for line in lines:
            if match := _VAT_UNLABELED.search(line.text):
                return _vat_field(match["value"], line, labeled=False, used=consumed)
        return Field.unknown()


def _vat_field(value: str, line: Line, *, labeled: bool, used: Consumed) -> Field[str]:
    used.add(line)
    if not is_valid_vat_format(value):
        reason = f"USt-IdNr. „{value}“ hat kein bekanntes Format"
        return Field.review(value, evidence("labeled", reason, Confidence.UNCERTAIN, line))
    if labeled:
        return Field.found(
            value, evidence("labeled", "Beschriftete USt-IdNr.", Confidence.CERTAIN, line)
        )
    reason = "USt-IdNr. am Format erkannt"
    return Field.found(value, evidence("pattern", reason, Confidence.LIKELY, line))
