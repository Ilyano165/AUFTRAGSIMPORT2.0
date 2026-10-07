"""Prüfungen über alle Zeilen: Dubletten bei Nummern und Aliasen, mehrdeutige Namen."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..domain.findings import Severity
from ..services.matching import normalize_name, normalize_number
from .mapping import ColumnMapping
from .model import CatalogField, CatalogIssue, IssueCode, MappedRow


def _rows(rows: list[int]) -> str:
    shown = ", ".join(str(r) for r in rows[:8])
    return shown + (f" … (+{len(rows) - 8})" if len(rows) > 8 else "")


def _mapping_issues(mapping: ColumnMapping | None) -> list[CatalogIssue]:
    if mapping is None:
        return []
    return [
        CatalogIssue(
            Severity.ERROR,
            IssueCode.MAPPING_MISSING,
            f"Keine Spalte für {m.label} zugeordnet",
            field=m,
        )
        for m in mapping.missing()
    ]


def _duplicate_numbers(rows: Sequence[MappedRow]) -> list[CatalogIssue]:
    numbers: dict[str, list[MappedRow]] = defaultdict(list)
    for row in rows:
        if row.article.number:
            numbers[normalize_number(row.article.number)].append(row)
    return [
        CatalogIssue(
            Severity.ERROR,
            IssueCode.NUMBER_DUPLICATE,
            f"Artikelnummer {same[0].article.number} kommt mehrfach vor "
            f"(Zeilen {_rows([r.row for r in same])})",
            same[0].row,
            CatalogField.NUMBER,
        )
        for same in numbers.values()
        if len(same) > 1
    ]


def _alias_issues(rows: Sequence[MappedRow]) -> list[CatalogIssue]:
    issues: list[CatalogIssue] = []
    owners: dict[str, set[str]] = defaultdict(set)
    first: dict[str, tuple[str, int]] = {}
    numbers = {
        normalize_number(r.article.number): r.article.number for r in rows if r.article.number
    }
    for row in rows:
        own = normalize_number(row.article.number)
        for alias in row.article.aliases:
            key = normalize_name(alias)
            owners[key].add(row.article.number)
            first.setdefault(key, (alias, row.row))
            other = numbers.get(normalize_number(alias))
            if other and normalize_number(other) != own:
                issues.append(
                    CatalogIssue(
                        Severity.WARNING,
                        IssueCode.ALIAS_CONFLICT,
                        f"Alias „{alias}“ ist zugleich Artikelnummer von {other}",
                        row.row,
                        CatalogField.ALIASES,
                    )
                )
    for key, articles in owners.items():
        if len(articles) > 1:
            alias, row_number = first[key]
            listed = ", ".join(sorted(articles))
            issues.append(
                CatalogIssue(
                    Severity.WARNING,
                    IssueCode.ALIAS_DUPLICATE,
                    f"Alias „{alias}“ gehört zu mehreren Artikeln ({listed}); "
                    "Treffer darüber werden immer zur Prüfung vorgelegt",
                    row_number,
                    CatalogField.ALIASES,
                )
            )
    return issues


def _ambiguous_names(rows: Sequence[MappedRow]) -> list[CatalogIssue]:
    names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.article.name:
            names[normalize_name(row.article.name)].add(row.article.number)
    return [
        CatalogIssue(
            Severity.WARNING,
            IssueCode.NAME_AMBIGUOUS,
            f"Gleicher Artikelname bei {', '.join(sorted(owners))}; Treffer über den Namen "
            "werden zur Prüfung vorgelegt",
            field=CatalogField.NAME,
        )
        for key, owners in names.items()
        if key and len(owners) > 1
    ]


def validate_rows(
    rows: Sequence[MappedRow], mapping: ColumnMapping | None = None
) -> list[CatalogIssue]:
    """Alle Befunde: Zuordnung, je Zeile und zeilenübergreifend; Fehler blockieren den Import."""
    issues = _mapping_issues(mapping)
    for row in rows:
        issues.extend(row.issues)
    issues += _duplicate_numbers(rows) + _alias_issues(rows) + _ambiguous_names(rows)
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(issues, key=lambda i: (order[i.severity], i.row or 0))
