"""Gemeinsame Typen: Katalogfelder, Befunde, zugeordnete Zeilen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..domain.findings import Severity
from ..domain.models import Article


class CatalogField(StrEnum):
    """Zielfelder eines Katalogartikels."""

    NUMBER = "number"
    NAME = "name"
    ALIASES = "aliases"
    PRICE = "price"
    TAX_RATE = "tax_rate"
    UNIT = "unit"
    ACTIVE = "active"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return FIELD_LABELS[self]


FIELD_LABELS = {
    CatalogField.NUMBER: "Artikelnummer",
    CatalogField.NAME: "Artikelname",
    CatalogField.ALIASES: "Alias",
    CatalogField.PRICE: "Preis",
    CatalogField.TAX_RATE: "Steuersatz",
    CatalogField.UNIT: "Einheit",
    CatalogField.ACTIVE: "aktiv/inaktiv",
}
REQUIRED_FIELDS = (CatalogField.NUMBER, CatalogField.NAME)


class IssueCode(StrEnum):
    """Art eines Katalogbefunds."""

    MAPPING_MISSING = "mapping_missing"
    NUMBER_MISSING = "number_missing"
    NUMBER_DUPLICATE = "number_duplicate"
    NAME_MISSING = "name_missing"
    PRICE_INVALID = "price_invalid"
    PRICE_CONVERSION = "price_conversion"
    TAX_INVALID = "tax_invalid"
    TAX_NOT_ALLOWED = "tax_not_allowed"
    TAX_MISSING = "tax_missing"
    ACTIVE_INVALID = "active_invalid"
    ALIAS_DUPLICATE = "alias_duplicate"
    ALIAS_CONFLICT = "alias_conflict"
    ALIAS_REPEATED = "alias_repeated"
    NAME_AMBIGUOUS = "name_ambiguous"
    VALUE_AMBIGUOUS = "value_ambiguous"


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    """Befund mit Ort; ``row`` ist die Zeilennummer in der Quelldatei."""

    severity: Severity
    code: IssueCode
    message: str
    row: int | None = None
    field: CatalogField | None = None

    def render(self) -> str:
        """Meldung mit Zeile und Feld, etwa „Zeile 12 · Preis: „12,5x“ ist keine Zahl“."""
        where = " · ".join(
            p
            for p in (
                f"Zeile {self.row}" if self.row else "",
                self.field.label if self.field else "",
            )
            if p
        )
        return f"{where}: {self.message}" if where else self.message


@dataclass(frozen=True, slots=True)
class MappedRow:
    """Artikel aus einer Quellzeile mit den Befunden dieser Zeile."""

    row: int
    article: Article
    issues: tuple[CatalogIssue, ...] = ()
