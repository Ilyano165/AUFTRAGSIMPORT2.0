"""Zuordnungsassistent: Spalten der Quelle auf Katalogfelder abbilden und Werte lesen."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from ..domain.findings import Severity
from ..domain.models import Article
from ..parsers.numbers import parse_decimal_de
from .model import CatalogField, CatalogIssue, IssueCode, MappedRow
from .table import RawTable

ALIAS_SEPARATORS = re.compile(r"[|;]")
MAX_PRICE = Decimal(10_000_000)
MAX_PRICE_DECIMALS = 4
PRICE_QUANTUM = Decimal("0.0001")
_NUMBER_IN_TEXT = re.compile(r"\d+(?:[.,]\d+)?")
_TRUE = frozenset({"1", "ja", "j", "true", "wahr", "x", "yes", "y", "aktiv", "active"})
_FALSE = frozenset(
    {"0", "nein", "n", "false", "falsch", "no", "inaktiv", "inactive", "gesperrt", "archiviert"}
)

SYNONYMS: dict[CatalogField, tuple[str, ...]] = {
    CatalogField.NUMBER: (
        "number",
        "artikelnummer",
        "artikel-nr.",
        "artikel-nr",
        "artikelnr",
        "artikelnr.",
        "art.-nr.",
        "art.-nr",
        "artnr",
        "art-nr",
        "nummer",
        "sku",
        "item number",
        "artikel nr",
    ),
    CatalogField.NAME: (
        "bezeichnung",
        "artikelbezeichnung",
        "artikelname",
        "name",
        "bezeichnung 1",
        "kurztext",
        "produktname",
        "beschreibung",
    ),
    CatalogField.ALIASES: (
        "aliases",
        "alias",
        "aliase",
        "suchbegriff",
        "suchbegriffe",
        "synonyme",
        "matchcode",
        "gtin",
        "ean",
        "ean-code",
        "gtin/ean",
    ),
    CatalogField.PRICE: (
        "price",
        "preis",
        "vk",
        "vk-preis",
        "vk preis",
        "verkaufspreis",
        "verkaufspreis netto",
        "vk netto",
        "vk-preis netto",
        "nettopreis",
        "preis netto",
        "einzelpreis",
        "verkaufspreis brutto",
        "bruttopreis",
        "preis brutto",
        "vk brutto",
    ),
    CatalogField.TAX_RATE: (
        "tax_rate",
        "steuersatz",
        "mwst",
        "mwst.",
        "ust",
        "mwst-satz",
        "mwst.-satz",
        "umsatzsteuer",
        "steuerart",
        "steuer",
        "steuer %",
        "ust-satz",
    ),
    CatalogField.UNIT: (
        "einheit",
        "me",
        "mengeneinheit",
        "einheit (me)",
        "unit",
        "verkaufseinheit",
    ),
    CatalogField.ACTIVE: (
        "active",
        "aktiv",
        "status",
        "aktiv/inaktiv",
        "inaktiv",
        "gesperrt",
        "archiviert",
    ),
}
INVERTED_ACTIVE = frozenset({"inaktiv", "gesperrt", "archiviert"})


class PriceBasis(StrEnum):
    """Ob Preise der Quelle netto oder brutto sind."""

    NET = "net"
    GROSS = "gross"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return "Netto" if self is PriceBasis.NET else "Brutto"


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Zuordnung Katalogfeld → Spaltenindizes (Alias darf mehrere Spalten haben)."""

    columns: Mapping[CatalogField, tuple[int, ...]] = field(default_factory=dict)
    price_basis: PriceBasis = PriceBasis.NET
    active_inverted: bool = False

    def column(self, target: CatalogField) -> int | None:
        """Erste zugeordnete Spalte."""
        indices = self.columns.get(target, ())
        return indices[0] if indices else None

    def with_columns(self, target: CatalogField, indices: Sequence[int]) -> ColumnMapping:
        """Kopie mit geänderter Zuordnung eines Felds."""
        columns = dict(self.columns)
        if indices:
            columns[target] = tuple(indices)
        else:
            columns.pop(target, None)
        return ColumnMapping(columns, self.price_basis, self.active_inverted)

    def missing(self) -> list[CatalogField]:
        """Pflichtfelder ohne Spalte."""
        return [f for f in (CatalogField.NUMBER, CatalogField.NAME) if f not in self.columns]

    def to_json(self, headers: Sequence[str]) -> dict[str, object]:
        """Speicherbare Form über Spaltennamen, damit der nächste Import sie wiedererkennt."""
        return {
            "columns": {
                f.value: [headers[i] for i in idx if i < len(headers)]
                for f, idx in self.columns.items()
            },
            "price_basis": self.price_basis.value,
            "active_inverted": self.active_inverted,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object], headers: Sequence[str]) -> ColumnMapping | None:
        """Gespeicherte Zuordnung, wenn alle Spalten in der neuen Quelle vorhanden sind."""
        raw = data.get("columns")
        if not isinstance(raw, dict) or not raw:
            return None
        columns: dict[CatalogField, tuple[int, ...]] = {}
        for key, names in raw.items():
            try:
                target = CatalogField(key)
            except ValueError:
                return None
            if not isinstance(names, list) or any(n not in headers for n in names):
                return None
            columns[target] = tuple(headers.index(n) for n in names)
        basis = PriceBasis.GROSS if data.get("price_basis") == "gross" else PriceBasis.NET
        return cls(columns, basis, bool(data.get("active_inverted")))


OWN_FIELDS = tuple(f.value for f in CatalogField)


def identity_mapping(headers: Sequence[str]) -> ColumnMapping:
    """Zuordnung für das eigene JSON-Format (Felder heißen wie die Katalogfelder)."""
    return ColumnMapping({CatalogField(h): (i,) for i, h in enumerate(headers) if h in OWN_FIELDS})


def _normalize_header(text: str) -> str:
    return " ".join(text.casefold().replace("*", "").rstrip(":").split())


def suggest_mapping(
    headers: Sequence[str], previous: Mapping[str, object] | None = None
) -> ColumnMapping:
    """Vorschlag: gespeicherte Zuordnung, sonst bekannte Spaltennamen (auch Lexware-Exporte)."""
    if previous and (reused := ColumnMapping.from_json(previous, headers)) is not None:
        return reused
    normalized = [_normalize_header(h) for h in headers]
    columns: dict[CatalogField, tuple[int, ...]] = {}
    taken: set[int] = set()
    for target, names in SYNONYMS.items():
        exact = [i for i, h in enumerate(normalized) if h in names and i not in taken]
        if not exact:
            exact = [
                i
                for i, h in enumerate(normalized)
                if i not in taken and any(h.startswith(n) for n in names if len(n) > 3)
            ]
        if not exact:
            continue
        chosen = tuple(exact) if target is CatalogField.ALIASES else (exact[0],)
        columns[target] = chosen
        taken.update(chosen)
    price = columns.get(CatalogField.PRICE)
    gross = bool(price) and "brutto" in normalized[price[0]]  # type: ignore[index]
    active = columns.get(CatalogField.ACTIVE)
    inverted = bool(active) and normalized[active[0]] in INVERTED_ACTIVE  # type: ignore[index]
    return ColumnMapping(columns, PriceBasis.GROSS if gross else PriceBasis.NET, inverted)


def _issue(  # noqa: PLR0917
    issues: list[CatalogIssue],
    severity: Severity,
    code: IssueCode,
    message: str,
    row: int,
    target: CatalogField,
) -> None:
    issues.append(CatalogIssue(severity, code, message, row, target))


def _decimal(
    raw: str, row: int, target: CatalogField, issues: list[CatalogIssue]
) -> Decimal | None:
    text = raw.replace("€", "").replace("EUR", "").replace("\xa0", " ").strip()
    if not text:
        return None
    negative = text.startswith("-")
    parsed = parse_decimal_de(text.lstrip("-").strip())
    if parsed is None:
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.PRICE_INVALID if target is CatalogField.PRICE else IssueCode.TAX_INVALID,
            f"„{raw}“ ist keine gültige Zahl",
            row,
            target,
        )
        return None
    if parsed.ambiguous:
        _issue(
            issues,
            Severity.WARNING,
            IssueCode.VALUE_AMBIGUOUS,
            f"„{raw}“ mehrdeutig geschrieben, gelesen als {parsed.value}",
            row,
            target,
        )
    return -parsed.value if negative else parsed.value


def _price(raw: str, row: int, issues: list[CatalogIssue]) -> Decimal | None:
    value = _decimal(raw, row, CatalogField.PRICE, issues)
    if value is None:
        return None
    if value < 0:
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.PRICE_INVALID,
            f"Preis {raw} ist negativ",
            row,
            CatalogField.PRICE,
        )
        return None
    if value > MAX_PRICE:
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.PRICE_INVALID,
            f"Preis {raw} ist unplausibel hoch",
            row,
            CatalogField.PRICE,
        )
        return None
    exponent = value.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > MAX_PRICE_DECIMALS:
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.PRICE_INVALID,
            f"Preis {raw} hat mehr als {MAX_PRICE_DECIMALS} Nachkommastellen",
            row,
            CatalogField.PRICE,
        )
        return None
    return value


def _tax(
    raw: str, row: int, allowed: Sequence[Decimal], issues: list[CatalogIssue]
) -> Decimal | None:
    if not raw.strip():
        return None
    found = _NUMBER_IN_TEXT.search(raw)
    if found is None:
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.TAX_INVALID,
            f"„{raw}“ enthält keinen Steuersatz",
            row,
            CatalogField.TAX_RATE,
        )
        return None
    value = _decimal(found.group(0), row, CatalogField.TAX_RATE, issues)
    if value is None:
        return None
    if Decimal(0) < value < Decimal(1):
        _issue(
            issues,
            Severity.WARNING,
            IssueCode.VALUE_AMBIGUOUS,
            f"„{raw}“ als {value * 100:g} % gelesen",
            row,
            CatalogField.TAX_RATE,
        )
        value *= 100
    value = Decimal(int(value)) if value == value.to_integral_value() else value.normalize()
    if allowed and value not in allowed:
        listed = ", ".join(f"{r:g}" for r in allowed)
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.TAX_NOT_ALLOWED,
            f"Steuersatz {value:g} % ist im Profil nicht hinterlegt (erlaubt: {listed} %)",
            row,
            CatalogField.TAX_RATE,
        )
        return None
    return value


def _active(raw: str, inverted: bool, row: int, issues: list[CatalogIssue]) -> bool:
    value = raw.strip().casefold()
    if not value:
        return not inverted
    if value in _TRUE:
        return not inverted
    if value in _FALSE:
        return inverted
    _issue(
        issues,
        Severity.ERROR,
        IssueCode.ACTIVE_INVALID,
        f"„{raw}“ ist weder aktiv noch inaktiv",
        row,
        CatalogField.ACTIVE,
    )
    return True


def _convert(  # noqa: PLR0917
    price: Decimal | None,
    tax: Decimal | None,
    mapping: ColumnMapping,
    target: PriceBasis,
    row: int,
    issues: list[CatalogIssue],
) -> Decimal | None:
    if price is None or mapping.price_basis is target:
        return price
    if tax is None:
        _issue(
            issues,
            Severity.ERROR,
            IssueCode.PRICE_CONVERSION,
            f"{mapping.price_basis.label}preis ohne Steuersatz nicht in {target.label} umrechenbar",
            row,
            CatalogField.PRICE,
        )
        return None
    factor = 1 + tax / 100
    value = price / factor if target is PriceBasis.NET else price * factor
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP).normalize()


def map_rows(
    table: RawTable,
    mapping: ColumnMapping,
    *,
    target_basis: PriceBasis,
    tax_rates: Sequence[Decimal],
) -> list[MappedRow]:
    """Liest alle Zeilen über die Zuordnung; Befunde je Zeile."""
    result: list[MappedRow] = []
    for row_number, cells in table.rows:
        issues: list[CatalogIssue] = []

        def cell(target: CatalogField, cells: tuple[str, ...] = cells) -> str:
            index = mapping.column(target)
            return cells[index].strip() if index is not None and index < len(cells) else ""

        aliases: list[str] = []
        for index in mapping.columns.get(CatalogField.ALIASES, ()):
            for part in ALIAS_SEPARATORS.split(cells[index] if index < len(cells) else ""):
                alias = part.strip()
                if not alias:
                    continue
                if alias.casefold() in (a.casefold() for a in aliases):
                    _issue(
                        issues,
                        Severity.INFO,
                        IssueCode.ALIAS_REPEATED,
                        f"Alias „{alias}“ doppelt angegeben, einmal übernommen",
                        row_number,
                        CatalogField.ALIASES,
                    )
                    continue
                aliases.append(alias)
        tax = _tax(cell(CatalogField.TAX_RATE), row_number, tax_rates, issues)
        price = _convert(
            _price(cell(CatalogField.PRICE), row_number, issues),
            tax,
            mapping,
            target_basis,
            row_number,
            issues,
        )
        number, name = cell(CatalogField.NUMBER), cell(CatalogField.NAME)
        if not number:
            _issue(
                issues,
                Severity.ERROR,
                IssueCode.NUMBER_MISSING,
                "Artikelnummer fehlt",
                row_number,
                CatalogField.NUMBER,
            )
        if not name:
            _issue(
                issues,
                Severity.ERROR,
                IssueCode.NAME_MISSING,
                "Artikelname fehlt",
                row_number,
                CatalogField.NAME,
            )
        if (
            mapping.column(CatalogField.TAX_RATE) is not None
            and tax is None
            and not any(i.field is CatalogField.TAX_RATE for i in issues)
        ):
            _issue(
                issues,
                Severity.WARNING,
                IssueCode.TAX_MISSING,
                "Steuersatz fehlt",
                row_number,
                CatalogField.TAX_RATE,
            )
        active = (
            _active(cell(CatalogField.ACTIVE), mapping.active_inverted, row_number, issues)
            if mapping.column(CatalogField.ACTIVE) is not None
            else True
        )
        article = Article(
            number=number,
            name=name,
            aliases=tuple(aliases),
            unit=cell(CatalogField.UNIT),
            tax_rate=tax,
            price=price,
            active=active,
        )
        result.append(MappedRow(row_number, article, tuple(issues)))
    return result
