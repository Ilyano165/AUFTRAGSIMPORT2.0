"""PaymentParser: Zahlungsart und IBAN, nur aus eindeutigen Angaben."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.models import PaymentMethod
from ..domain.provenance import Confidence, Field
from .common import Consumed, ParseContext, evidence
from .text import Line

_LABEL = re.compile(
    r"^[-*•\s]*(?:zahlungs(?:art|weise|methode|bedingung(?:en)?)|zahlart|bezahlung|zahlung"
    r"|payment(?:\s*method)?)\s*[:=]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_KEYWORDS: tuple[tuple[re.Pattern[str], PaymentMethod], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), method)
    for pattern, method in (
        (r"nachnahme|cash on delivery", PaymentMethod.CASH_ON_DELIVERY),
        (
            r"vor(?:aus)?kasse|vorab\s*überweisung|überweisung\s*vorab|prepayment",
            PaymentMethod.PREPAYMENT,
        ),
        (r"lastschrift|bankeinzug|sepa|abbuchung|direct debit", PaymentMethod.DIRECT_DEBIT),
        (r"\bbar(?:zahlung)?\b|\bcash\b", PaymentMethod.CASH),
        (r"rechnung|invoice|\bziel\b|\d+\s*tage\s*netto|netto\s*\d+\s*tage", PaymentMethod.INVOICE),
    )
)
_FREE_TEXT = re.compile(
    r"\b(?:per|gegen|als|auf|mit)\s+(?P<word>nachnahme|vor(?:aus)?kasse|lastschrift|rechnung|bankeinzug)\b",
    re.IGNORECASE,
)
_IBAN = re.compile(
    r"\biban\s*[:.]?\s*(?P<value>[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30})", re.IGNORECASE
)
IBAN_MODULUS = 97


def is_valid_iban(value: str) -> bool:
    """Prüft die IBAN-Prüfsumme nach ISO 13616."""
    compact = value.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % IBAN_MODULUS == 1


def methods_in(text: str) -> list[PaymentMethod]:
    """Alle Zahlungsarten, die in ``text`` genannt werden, in Prioritätsreihenfolge."""
    return [method for pattern, method in _KEYWORDS if pattern.search(text)]


@dataclass(frozen=True, slots=True)
class PaymentResult:
    """Zahlungsart und IBAN."""

    method: Field[PaymentMethod]
    iban: Field[str]


class PaymentParser:
    """Beschriftete Angaben gelten als sicher, Formulierungen im Fließtext als unsicher."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> PaymentResult:
        """Widersprüche führen zur Prüfung; Signaturen werden ignoriert."""
        labeled: list[tuple[PaymentMethod, Line]] = []
        unmapped: list[Line] = []
        free: list[tuple[PaymentMethod, Line]] = []
        for line in ctx.body():
            if match := _LABEL.match(line.text):
                consumed.add(line)
                found = methods_in(match["value"])
                labeled.extend((method, line) for method in found)
                if not found:
                    unmapped.append(line)
            else:
                for word in _FREE_TEXT.finditer(line.text):
                    free.extend((method, line) for method in methods_in(word["word"]))
        return PaymentResult(self._method(labeled, unmapped, free), self._iban(ctx, consumed))

    @staticmethod
    def _method(
        labeled: list[tuple[PaymentMethod, Line]],
        unmapped: list[Line],
        free: list[tuple[PaymentMethod, Line]],
    ) -> Field[PaymentMethod]:
        distinct = list(dict.fromkeys(method for method, _ in labeled))
        if len(distinct) == 1:
            method, line = labeled[0]
            others = {m for m, _ in free} - {method}
            if not others:
                return Field.found(
                    method,
                    evidence("labeled", "Beschriftete Zahlungsart", Confidence.CERTAIN, line),
                )
            distinct += sorted(others)
        if len(distinct) > 1:
            reason = "Widersprüchliche Zahlungsarten: " + ", ".join(m.label for m in distinct)
            source = [line for _, line in labeled + free]
            return Field.review(
                distinct[0],
                evidence("conflict", reason, Confidence.UNCERTAIN, *source),
                tuple(distinct),
            )
        if unmapped:
            reason = f"Zahlungsangabe „{unmapped[0].text}“ keiner Zahlungsart zuordenbar"
            return Field.review(
                None, evidence("labeled", reason, Confidence.UNCERTAIN, unmapped[0])
            )
        free_distinct = list(dict.fromkeys(method for method, _ in free))
        if free_distinct:
            method, line = free[0]
            reason = f"Zahlungsart „{method.label}“ nur im Fließtext erwähnt"
            return Field.review(
                method,
                evidence("free_text", reason, Confidence.UNCERTAIN, line),
                tuple(free_distinct),
            )
        return Field.unknown("Keine Zahlungsart angegeben")

    @staticmethod
    def _iban(ctx: ParseContext, consumed: Consumed) -> Field[str]:
        for line in ctx.body():
            if match := _IBAN.search(line.text):
                consumed.add(line)
                value = match["value"].replace(" ", "").upper()
                if is_valid_iban(value):
                    return Field.found(
                        value, evidence("labeled", "Beschriftete IBAN", Confidence.CERTAIN, line)
                    )
                reason = "IBAN-Prüfsumme ungültig"
                return Field.review(value, evidence("labeled", reason, Confidence.UNCERTAIN, line))
        return Field.unknown()
