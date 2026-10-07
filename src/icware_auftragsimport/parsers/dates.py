"""DateParser: Bestelldatum und Wunschliefertermin."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..domain.extraction import ExtractionNote
from ..domain.provenance import Confidence, Evidence, Field
from .common import Consumed, ParseContext, evidence, note
from .text import Line

CENTURY = 2000
MONTHS = {
    "januar": 1, "jan": 1, "jänner": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3,
    "mär": 3, "mrz": 3, "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7,
    "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
    "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}  # fmt: skip
_NUMERIC = re.compile(r"\b(?P<d>\d{1,2})\.\s?(?P<m>\d{1,2})\.\s?(?P<y>\d{4}|\d{2})\b")
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_TEXTUAL = re.compile(r"\b(?P<d>\d{1,2})\.\s*(?P<mon>[A-Za-zäÄ]{3,9})\.?\s+(?P<y>\d{4})\b")
_WEEK = re.compile(r"\b(?:kw|kalenderwoche)\s*(?P<w>\d{1,2})\b", re.IGNORECASE)
_ORDER_LABEL = re.compile(
    r"^[-*•\s]*(?P<label>bestelldatum|auftragsdatum|datum der bestellung|order date|datum)"
    r"\s*[:=]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_DELIVERY_LABEL = re.compile(
    r"^[-*•\s]*(?:gewünschter\s+)?(?:wunsch)?(?:liefertermin|lieferdatum|anliefertermin|"
    r"wunschtermin|lieferwunsch|anlieferung|lieferung bis|liefern bis|delivery date)"
    r"\s*[:=]?\s*(?P<value>.+)$",
    re.IGNORECASE,
)


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_text(text: str) -> date | None:
    """Erstes gültiges Datum in ``text`` oder ``None``; ungültige Tage werden verworfen."""
    if match := _ISO.search(text):
        return _make(int(match["y"]), int(match["m"]), int(match["d"]))
    if match := _NUMERIC.search(text):
        year = int(match["y"])
        return _make(year + CENTURY if year < 100 else year, int(match["m"]), int(match["d"]))
    if (match := _TEXTUAL.search(text)) and (month := MONTHS.get(match["mon"].casefold())):
        return _make(int(match["y"]), month, int(match["d"]))
    return None


@dataclass(frozen=True, slots=True)
class DateResult:
    """Bestelldatum, Wunschtermin und Hinweise."""

    order_date: Field[date]
    requested_delivery: Field[date]
    notes: tuple[ExtractionNote, ...]


class DateParser:
    """Liest nur beschriftete Daten; ohne Bestelldatum gilt das Maildatum (wahrscheinlich)."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> DateResult:
        """Kalenderwochen und ungültige Daten werden zur Prüfung vorgelegt."""
        order_date: Field[date] = Field.unknown()
        delivery: Field[date] = Field.unknown()
        notes: list[ExtractionNote] = []
        for line in ctx.body():
            if not order_date.has_value and (match := _ORDER_LABEL.match(line.text)):
                generic = match["label"].casefold() == "datum"
                order_date = self._field(line, match["value"], generic, notes, "Bestelldatum")
                consumed.add(line)
            elif not delivery.has_value and (match := _DELIVERY_LABEL.match(line.text)):
                delivery = self._field(line, match["value"], False, notes, "Liefertermin")
                consumed.add(line)
        if not order_date.has_value and ctx.mail_date is not None:
            order_date = Field.found(
                ctx.mail_date,
                Evidence(
                    "mail_date_header",
                    "Kein Bestelldatum in der Mail; Eingangsdatum der Mail verwendet",
                    Confidence.LIKELY,
                ),
            )
        return DateResult(order_date, delivery, tuple(notes))

    @staticmethod
    def _field(
        line: Line, raw: str, generic: bool, notes: list[ExtractionNote], what: str
    ) -> Field[date]:
        value = parse_date_text(raw)
        if value is not None:
            confidence = Confidence.LIKELY if generic else Confidence.CERTAIN
            return Field.found(
                value, evidence("labeled", f"Beschriftetes {what}", confidence, line)
            )
        week = _WEEK.search(raw)
        reason = (
            f"{what} als Kalenderwoche {week['w']} angegeben; bitte konkretes Datum festlegen"
            if week
            else f"{what} „{raw.strip()}“ ist kein gültiges Datum"
        )
        notes.append(note("DATE_UNPARSED", reason, line))
        return Field.review(None, evidence("labeled", reason, Confidence.UNCERTAIN, line))
