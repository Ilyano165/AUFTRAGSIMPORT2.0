"""OrderNumberParser: Bestellnummer des Kunden."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.identity import normalize_reference
from ..domain.provenance import Confidence, Evidence, Field
from .common import Consumed, ParseContext, evidence

_VALUE = r"(?P<value>[A-Za-z0-9][A-Za-z0-9\-/._]{0,39})"
_LABELED = re.compile(
    r"^[-*•\s]*(?:ihre\s+|unsere\s+|kunden)?(?:"
    r"bestell(?:ungs)?\s*-?\s*(?:nummer|nr\.?|no\.?)|bestellung\s+(?:nr\.?|nummer)"
    r"|auftrags\s*-?\s*(?:nummer|nr\.?)|po(?:\s*-?\s*(?:nummer|nr\.?|no\.?|number))?"
    r"|order\s*(?:number|no\.?|\#)|bestellreferenz|kundenbestellnummer|ihr\s+zeichen"
    rf")(?![a-zäöüß])\s*(?:lautet\s*)?[:#=]?\s*{_VALUE}",
    re.IGNORECASE,
)
_SUBJECT = re.compile(
    r"\b(?:bestellung|bestell(?:nummer|-?nr\.?)|auftrag(?:snummer)?|order|po)\b"
    rf"\s*(?:nr\.?|nummer|no\.?|\#)?\s*[:#]?\s*{_VALUE}",
    re.IGNORECASE,
)
_DATE_LIKE = re.compile(r"\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})?")
_HAS_DIGIT = re.compile(r"\d")


def _usable(value: str) -> bool:
    return bool(_HAS_DIGIT.search(value)) and not _DATE_LIKE.fullmatch(value)


@dataclass(frozen=True, slots=True)
class OrderNumberResult:
    """Erkannte Bestellnummer."""

    field: Field[str]


class OrderNumberParser:
    """Liest beschriftete Bestellnummern aus dem Text, ersatzweise aus dem Betreff."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> OrderNumberResult:
        """Mehrere verschiedene Nummern führen zur Prüfung, nie zur stillen Auswahl."""
        found: dict[str, tuple[str, Evidence]] = {}
        for line in ctx.body():
            match = _LABELED.match(line.text)
            if match and _usable(match.group("value").rstrip("./")):
                value = match.group("value").rstrip("./")
                found.setdefault(
                    normalize_reference(value),
                    (
                        value,
                        evidence("labeled", "Beschriftete Bestellnummer", Confidence.CERTAIN, line),
                    ),
                )
                consumed.add(line)
        subject_value = self._from_subject(ctx.subject)
        if subject_value and normalize_reference(subject_value) not in found and not found:
            reason = f"Aus dem Betreff „{ctx.subject.strip()}“"
            return OrderNumberResult(
                Field.found(subject_value, Evidence("subject", reason, Confidence.LIKELY))
            )
        if subject_value and normalize_reference(subject_value) not in found:
            found[normalize_reference(subject_value)] = (
                subject_value,
                Evidence("subject", "Aus dem Betreff", Confidence.LIKELY),
            )
        if not found:
            return OrderNumberResult(Field.unknown("Keine Bestellnummer des Kunden gefunden"))
        values = list(found.values())
        if len(values) == 1:
            value, first_evidence = values[0]
            return OrderNumberResult(Field.found(value, first_evidence))
        reason = "Mehrere verschiedene Bestellnummern: " + ", ".join(v for v, _ in values)
        first_value, first_evidence = values[0]
        conflict = Evidence("conflict", reason, Confidence.UNCERTAIN, first_evidence.source)
        return OrderNumberResult(Field.review(first_value, conflict, tuple(v for v, _ in values)))

    @staticmethod
    def _from_subject(subject: str) -> str | None:
        for match in _SUBJECT.finditer(subject):
            value = match.group("value").rstrip("./")
            if _usable(value):
                return value
        return None
