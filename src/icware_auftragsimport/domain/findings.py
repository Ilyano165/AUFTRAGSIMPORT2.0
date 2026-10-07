"""Ergebnisse der Validierung."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """Fehler blockieren den Export, Warnungen und Hinweise nicht."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung für die Oberfläche."""
        return _SEVERITY_LABELS[self]


_SEVERITY_LABELS = {Severity.ERROR: "Fehler", Severity.WARNING: "Warnung", Severity.INFO: "Hinweis"}


@dataclass(frozen=True, slots=True)
class Finding:
    """Ein Befund mit Feldbezug.

    ``acknowledgeable`` markiert Fehler, die der Benutzer nach Prüfung bewusst
    freigeben darf, etwa ein mögliches Duplikat. Alle anderen Fehler müssen behoben werden.
    """

    code: str
    severity: Severity
    message: str
    field: str = ""
    hint: str = ""
    acknowledgeable: bool = False

    @property
    def key(self) -> str:
        """Stabiler Schlüssel zum Bestätigen dieses Befunds."""
        return f"{self.code}:{self.field}"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Alle Befunde zu einer Auftragsrevision."""

    findings: tuple[Finding, ...] = ()

    def errors(self, acknowledged: frozenset[str] = frozenset()) -> tuple[Finding, ...]:
        """Blockierende Befunde, die nicht bewusst bestätigt wurden."""
        return tuple(
            f
            for f in self.findings
            if f.severity is Severity.ERROR and not (f.acknowledgeable and f.key in acknowledged)
        )

    def warnings(self) -> tuple[Finding, ...]:
        """Nicht blockierende Warnungen."""
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    def is_exportable(self, acknowledged: frozenset[str] = frozenset()) -> bool:
        """True, wenn kein offener Fehler übrig ist."""
        return not self.errors(acknowledged)

    def for_field(self, field: str) -> tuple[Finding, ...]:
        """Befunde zu einem Feld, etwa ``invoice_address`` oder ``lines[2].quantity``."""
        return tuple(
            f for f in self.findings if f.field == field or f.field.startswith(f"{field}.")
        )
