"""Gemeinsame Bausteine der Parser."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from ..domain.extraction import ExtractionNote
from ..domain.models import Address
from ..domain.provenance import Confidence, Evidence, SourceRef
from .text import MAX_EXCERPT, Line, LineKind, NormalizedText

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BULLET = re.compile(r"^(?:[-*•·–]\s*|(?:pos(?:ition)?\.?\s*)?\d{1,3}[.):]\s+(?=\d))")
NAME_PARTICLES = frozenset({"von", "van", "de", "der", "zu", "vom", "ter", "den", "da", "di"})
PERSON = re.compile(
    r"^(?:(?:Dr|Prof|Dipl\.-\w+)\.?\s+)*[A-ZÄÖÜ][a-zäöüß'’-]+"
    r"(?:\s+(?:von|van|de|der|zu|vom|ter|den|da|di))*(?:\s+[A-ZÄÖÜ][a-zäöüß'’-]+){1,2}$"
)
_CONFIDENCE_ORDER = (Confidence.UNCERTAIN, Confidence.LIKELY, Confidence.CERTAIN)


@dataclass(frozen=True, slots=True)
class ParseContext:
    """Eingabe aller Parser für eine Mail."""

    text: NormalizedText
    subject: str
    sender_email: str
    sender_name: str = ""
    mail_date: date | None = None
    own_addresses: tuple[Address, ...] = ()

    def body(self) -> tuple[Line, ...]:
        """Nur Bestelltext ohne Signatur."""
        return tuple(line for line in self.text.lines if line.kind is LineKind.BODY)

    def content(self) -> tuple[Line, ...]:
        """Bestelltext, Signatur und Leerzeilen."""
        return self.text.content()

    def customer_sender(self) -> tuple[str, str, bool]:
        """(Mailadresse, Name, aus Weiterleitung) des eigentlichen Bestellers."""
        if self.text.forwarded and self.text.original_sender:
            return self.text.original_sender, self.text.original_sender_name, True
        return self.sender_email.casefold(), self.sender_name, False


@dataclass(slots=True)
class Consumed:
    """Zeilen, die ein Parser verbraucht hat und die kein anderer mehr deuten soll."""

    numbers: set[int] = field(default_factory=set)

    def add(self, *lines: Line) -> None:
        """Markiert Zeilen als verbraucht."""
        self.numbers.update(line.no for line in lines)

    def __contains__(self, line: object) -> bool:
        return isinstance(line, Line) and line.no in self.numbers


def span(lines: Sequence[Line]) -> SourceRef | None:
    """Fundstelle über mehrere Zeilen."""
    if not lines:
        return None
    first, last = min(line.no for line in lines), max(line.no for line in lines)
    excerpt = " | ".join(line.text for line in lines)[:MAX_EXCERPT]
    return SourceRef(first, last, excerpt)


def evidence(method: str, reason: str, confidence: Confidence, *lines: Line) -> Evidence:
    """Begründung mit Fundstelle."""
    return Evidence(method, reason, confidence, span(lines))


def weakest(confidences: Iterable[Confidence]) -> Confidence:
    """Schwächste Sicherheit einer Menge; leere Menge gilt als sicher."""
    ranked = [_CONFIDENCE_ORDER.index(c) for c in confidences]
    return _CONFIDENCE_ORDER[min(ranked)] if ranked else Confidence.CERTAIN


def strip_bullet(text: str) -> str:
    """Entfernt Aufzählungszeichen und Positionsnummern am Zeilenanfang."""
    return BULLET.sub("", text, count=1).strip()


def note(code: str, message: str, line: Line | None = None) -> ExtractionNote:
    """Parserhinweis mit optionaler Fundstelle."""
    return ExtractionNote(code, message, line.ref() if line else None)


def split_person_name(text: str) -> tuple[str, str, Confidence]:
    """Teilt ``Vorname Nachname``; nur eindeutige Fälle gelten als wahrscheinlich."""
    tokens = [t for t in text.replace(",", " ").split() if not t.endswith(".")]
    if len(tokens) < 2:
        return "", text.strip(), Confidence.UNCERTAIN
    if len(tokens) == 2:
        return tokens[0], tokens[1], Confidence.LIKELY
    if tokens[1].casefold() in NAME_PARTICLES:
        return tokens[0], " ".join(tokens[1:]), Confidence.LIKELY
    return tokens[0], " ".join(tokens[1:]), Confidence.UNCERTAIN
