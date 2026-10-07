"""Herkunft, Sicherheit und Bearbeitungszustand erkannter Werte."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class FieldState(StrEnum):
    """Bearbeitungszustand eines Werts."""

    RECOGNIZED = "recognized"
    NEEDS_REVIEW = "needs_review"
    UNKNOWN = "unknown"
    MANUAL = "manual"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung für die Oberfläche."""
        return _FIELD_STATE_LABELS[self]


_FIELD_STATE_LABELS = {
    FieldState.RECOGNIZED: "erkannt",
    FieldState.NEEDS_REVIEW: "prüfen",
    FieldState.UNKNOWN: "unbekannt",
    FieldState.MANUAL: "manuell",
}


class Confidence(StrEnum):
    """Wie sicher eine Regel ihren Treffer einschätzt."""

    CERTAIN = "certain"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Stelle im normalisierten Mailtext; Zeilennummern beginnen bei 1."""

    line_start: int
    line_end: int
    excerpt: str = ""

    def __post_init__(self) -> None:
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError(f"Ungültiger Zeilenbereich {self.line_start}-{self.line_end}")

    @property
    def label(self) -> str:
        """Kurze Angabe wie ``Mailzeile 18`` oder ``Mailzeilen 18–20``."""
        if self.line_start == self.line_end:
            return f"Mailzeile {self.line_start}"
        return f"Mailzeilen {self.line_start}–{self.line_end}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """Begründung eines Werts: Regel, Erklärung, Sicherheit und Fundstelle."""

    method: str
    reason: str
    confidence: Confidence
    source: SourceRef | None = None

    def describe(self) -> str:
        """Erklärung mit Fundstelle für Tooltip und Prüfansicht."""
        if self.source is None:
            return self.reason
        return f"{self.reason} ({self.source.label})"


MANUAL_EVIDENCE = Evidence(
    method="manual", reason="Vom Benutzer eingegeben", confidence=Confidence.CERTAIN
)


@dataclass(frozen=True, slots=True)
class Field[T]:
    """Ein Wert mit Zustand, Begründung und möglichen Alternativen."""

    value: T | None = None
    state: FieldState = FieldState.UNKNOWN
    evidence: Evidence | None = None
    candidates: tuple[T, ...] = ()

    @classmethod
    def found(cls, value: T, evidence: Evidence) -> Field[T]:
        """Treffer einer Regel; unsichere Treffer landen automatisch in der Prüfung."""
        state = (
            FieldState.NEEDS_REVIEW
            if evidence.confidence is Confidence.UNCERTAIN
            else FieldState.RECOGNIZED
        )
        return cls(value=value, state=state, evidence=evidence)

    @classmethod
    def review(
        cls, value: T | None, evidence: Evidence, candidates: tuple[T, ...] = ()
    ) -> Field[T]:
        """Wert oder Vorschlag, der ausdrücklich bestätigt werden muss."""
        return cls(
            value=value, state=FieldState.NEEDS_REVIEW, evidence=evidence, candidates=candidates
        )

    @classmethod
    def unknown(cls, reason: str = "") -> Field[T]:
        """Kein Wert erkannt; ``reason`` erklärt optional, warum."""
        evidence = Evidence("not_found", reason, Confidence.UNCERTAIN) if reason else None
        return cls(value=None, state=FieldState.UNKNOWN, evidence=evidence)

    @classmethod
    def manual(cls, value: T | None) -> Field[T]:
        """Vom Benutzer gesetzter oder bestätigter Wert."""
        return cls(value=value, state=FieldState.MANUAL, evidence=MANUAL_EVIDENCE)

    @property
    def needs_attention(self) -> bool:
        """True, wenn der Wert fehlt oder bestätigt werden muss."""
        return self.state in (FieldState.NEEDS_REVIEW, FieldState.UNKNOWN)

    @property
    def has_value(self) -> bool:
        """True, wenn ein nicht-leerer Wert vorliegt."""
        return self.value is not None and self.value != ""
