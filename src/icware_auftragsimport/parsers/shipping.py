"""ShippingParser: Versandart und Versandkosten."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ..domain.provenance import Confidence, Field, FieldState
from .common import Consumed, ParseContext, evidence
from .numbers import parse_decimal_de
from .text import Line

_LABEL = re.compile(
    r"^[-*•\s]*(?:versand(?:art|weg)?|liefer(?:art|weg)|lieferung\s+per|zustellung|"
    r"shipping(?:\s*method)?)\s*[:=]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
CARRIERS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name)
    for pattern, name in (
        (r"\bdhl\s*express\b", "DHL Express"),
        (r"\bdhl\b", "DHL"),
        (r"\bups\b", "UPS"),
        (r"\bdpd\b", "DPD"),
        (r"\bgls\b", "GLS"),
        (r"\bhermes\b", "Hermes"),
        (r"\bfedex\b", "FedEx"),
        (r"\btnt\b", "TNT"),
        (r"\bspedition\b", "Spedition"),
        (r"\b(?:selbst)?abholung\b|\babholer\b|\bholen\s+(?:wir|es)\s+ab\b", "Abholung"),
    )
)
_FREE_TEXT = re.compile(
    r"\b(?:per|mit|über|via)\s+(?P<carrier>dhl|ups|dpd|gls|hermes|fedex|tnt|spedition)\b",
    re.IGNORECASE,
)
_FEE = re.compile(
    r"^[-*•\s]*(?:versandkosten|versandpauschale|porto|fracht(?:kosten)?|lieferkosten|"
    r"transportkosten)\s*[:=]?\s*(?P<amount>\d[\d.,]*)\s*(?:€|eur(?:o)?)?\s*$",
    re.IGNORECASE,
)


def carrier_in(text: str) -> str | None:
    """Versandart zu einem Text oder ``None``."""
    for pattern, name in CARRIERS:
        if pattern.search(text):
            return name
    return None


@dataclass(frozen=True, slots=True)
class ShippingResult:
    """Versandart und Versandkosten."""

    method: Field[str]
    fee: Field[Decimal]


class ShippingParser:
    """Nur „Versand: …“ mit Doppelpunkt gilt als Angabe; „Versand am 14.06.“ nicht."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> ShippingResult:
        """Freitexterwähnungen wie „per UPS“ werden nur als Vorschlag übernommen."""
        method: Field[str] = Field.unknown()
        fee: Field[Decimal] = Field.unknown()
        free_hit: tuple[str, Line] | None = None
        for line in ctx.body():
            if not fee.has_value and (match := _FEE.match(line.text)):
                fee = self._fee(match["amount"], line)
                consumed.add(line)
            elif not method.has_value and (match := _LABEL.match(line.text)):
                method = self._labeled(match["value"], line)
                consumed.add(line)
            elif free_hit is None and (match := _FREE_TEXT.search(line.text)):
                carrier = carrier_in(match["carrier"])
                free_hit = (carrier, line) if carrier else None
        if method.state is FieldState.UNKNOWN and free_hit is not None:
            carrier, line = free_hit
            reason = f"Versandart „{carrier}“ nur im Fließtext erwähnt"
            method = Field.review(
                carrier, evidence("free_text", reason, Confidence.UNCERTAIN, line)
            )
        return ShippingResult(method, fee)

    @staticmethod
    def _labeled(value: str, line: Line) -> Field[str]:
        carrier = carrier_in(value)
        if carrier:
            return Field.found(
                carrier, evidence("labeled", "Beschriftete Versandart", Confidence.CERTAIN, line)
            )
        reason = f"Versandangabe „{value.strip()}“ keiner Versandart zuordenbar"
        return Field.review(None, evidence("labeled", reason, Confidence.UNCERTAIN, line))

    @staticmethod
    def _fee(raw: str, line: Line) -> Field[Decimal]:
        number = parse_decimal_de(raw)
        if number is None:
            reason = f"Versandkosten „{raw}“ nicht lesbar"
            return Field.review(None, evidence("labeled", reason, Confidence.UNCERTAIN, line))
        confidence = Confidence.UNCERTAIN if number.ambiguous else Confidence.CERTAIN
        reason = "Beschriftete Versandkosten" + (
            " (Schreibweise mehrdeutig)" if number.ambiguous else ""
        )
        return Field.found(number.value, evidence("labeled", reason, confidence, line))
