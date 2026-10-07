"""Artikelkatalog: Import, Validierung und Versionierung.

Das Exportformat des Lexware-Artikelstamms ist noch nicht belegt (Integrationsabhängigkeit
T-06). Deshalb liest der Import CSV mit erkennbaren oder frei zugeordneten Spalten sowie
ein eigenes JSON-Format; ein Katalog mit Fehlern wird nie gespeichert.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..domain.errors import CatalogError, UserMessage
from ..domain.findings import Severity
from ..domain.models import Article
from ..domain.ports import Clock
from ..infrastructure.db import transaction
from ..infrastructure.repositories import CatalogRepository, JournalRepository
from ..parsers.numbers import parse_decimal_de
from ..parsers.text import decode_payload
from ..security.limits import FILES
from .matching import Catalog, normalize_name, normalize_number

MAX_ISSUES_IN_MESSAGE = 10
MAX_TAX_RATE = Decimal(100)
ALIAS_SEPARATOR = "|"
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "number": ("artikelnummer", "artnr", "art.-nr.", "art-nr", "artikel-nr.", "nummer", "sku"),
    "name": ("bezeichnung", "artikelbezeichnung", "name", "artikelname", "beschreibung"),
    "aliases": ("alias", "aliase", "suchbegriffe", "synonyme"),
    "unit": ("einheit", "me", "mengeneinheit"),
    "tax_rate": ("steuersatz", "mwst", "ust", "mwst-satz", "mwst.-satz"),
    "price": ("preis", "vk", "vk-preis", "verkaufspreis", "einzelpreis"),
    "active": ("aktiv",),
}
_TRUE_VALUES = frozenset({"1", "ja", "j", "true", "x", "yes", "aktiv"})
_FALSE_VALUES = frozenset({"0", "nein", "n", "false", "no", "inaktiv", ""})


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    """Problem beim Katalogimport; ``row`` zählt ab der Kopfzeile als Zeile 1."""

    severity: Severity
    message: str
    row: int | None = None

    def render(self) -> str:
        """Meldung mit Zeilenangabe."""
        return f"Zeile {self.row}: {self.message}" if self.row else self.message


@dataclass(frozen=True, slots=True)
class CatalogImport:
    """Gelesene Artikel mit allen Befunden."""

    articles: tuple[Article, ...]
    issues: tuple[CatalogIssue, ...]
    source_sha256: str

    @property
    def errors(self) -> tuple[CatalogIssue, ...]:
        """Blockierende Befunde."""
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)


def _column(headers: Sequence[str], role: str, explicit: dict[str, str]) -> int | None:
    wanted = [explicit[role].casefold()] if role in explicit else list(COLUMN_ALIASES[role])
    normalized = [h.strip().casefold() for h in headers]
    for name in wanted:
        if name in normalized:
            return normalized.index(name)
    return None


def _decimal(raw: str, what: str, row: int, issues: list[CatalogIssue]) -> Decimal | None:
    text = raw.strip().rstrip("%").strip()
    if not text:
        return None
    negative = text.startswith("-")
    number = parse_decimal_de(text.lstrip("-").strip())
    if number is None:
        issues.append(CatalogIssue(Severity.ERROR, f"{what} „{raw}“ ist keine Zahl", row))
        return None
    if number.ambiguous:
        issues.append(CatalogIssue(Severity.WARNING, f"{what} „{raw}“ mehrdeutig geschrieben", row))
    return -number.value if negative else number.value


def _active(raw: str, row: int, issues: list[CatalogIssue]) -> bool:
    value = raw.strip().casefold()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    issues.append(CatalogIssue(Severity.ERROR, f"Aktiv-Wert „{raw}“ unbekannt", row))
    return True


def _delimiter(text: str) -> str:
    try:
        first = text.split("\n", 1)[0][: FILES.catalog_max_field_chars]
        return csv.Sniffer().sniff(first, delimiters=";,\t").delimiter
    except csv.Error:
        return ";"


def _row_article(
    row: Sequence[str], index: dict[str, int | None], row_no: int, issues: list[CatalogIssue]
) -> Article:
    def cell(role: str) -> str:
        position = index[role]
        return row[position].strip() if position is not None and position < len(row) else ""

    aliases = tuple(a.strip() for a in cell("aliases").split(ALIAS_SEPARATOR) if a.strip())
    return Article(
        number=cell("number"),
        name=cell("name"),
        aliases=aliases,
        unit=cell("unit"),
        tax_rate=_decimal(cell("tax_rate"), "Steuersatz", row_no, issues),
        price=_decimal(cell("price"), "Preis", row_no, issues),
        active=_active(cell("active"), row_no, issues) if index["active"] is not None else True,
    )


def _check_size(data: bytes) -> None:
    if len(data) > FILES.catalog_max_bytes:
        raise _catalog_error([f"Datei größer als {FILES.catalog_max_bytes // (1024 * 1024)} MB"])


def _bounded_rows(text: str) -> list[list[str]]:
    """Liest höchstens ``catalog_max_rows`` Zeilen mit begrenzter Feldlänge."""
    previous = csv.field_size_limit(FILES.catalog_max_field_chars)
    rows: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(text), delimiter=_delimiter(text)):
            if len(rows) >= FILES.catalog_max_rows:
                raise _catalog_error([f"Mehr als {FILES.catalog_max_rows} Zeilen"])
            rows.append(row)
    except csv.Error as exc:
        raise _catalog_error([f"CSV fehlerhaft oder Feld zu lang: {exc}"]) from exc
    finally:
        csv.field_size_limit(previous)
    return rows


def read_catalog_csv(data: bytes, columns: dict[str, str] | None = None) -> CatalogImport:
    """Liest CSV (``;``, ``,`` oder Tab; UTF-8 oder Windows-1252).

    ``columns`` ordnet Rollen (``number``, ``name``, ``aliases``, ``unit``, ``tax_rate``,
    ``price``, ``active``) abweichenden Spaltenüberschriften zu.
    """
    _check_size(data)
    sha = hashlib.sha256(data).hexdigest()
    text = decode_payload(data.removeprefix(b"\xef\xbb\xbf"), "utf-8").text
    rows = _bounded_rows(text)
    if not rows:
        raise _catalog_error(["Die Datei ist leer"])
    explicit = {k: v for k, v in (columns or {}).items() if v}
    index = {role: _column(rows[0], role, explicit) for role in COLUMN_ALIASES}
    labels = {"number": "Artikelnummer", "name": "Bezeichnung"}
    missing = [role for role in labels if index[role] is None]
    if missing:
        header = "; ".join(rows[0])
        raise _catalog_error(
            [f"Spalte für {labels[m]} nicht gefunden (Kopfzeile: {header})" for m in missing]
        )
    issues: list[CatalogIssue] = []
    articles = [
        _row_article(row, index, row_no, issues)
        for row_no, row in enumerate(rows[1:], start=2)
        if any(cell.strip() for cell in row)
    ]
    issues += validate_catalog(articles, first_row=2)
    return CatalogImport(tuple(articles), tuple(issues), sha)


def read_catalog_json(data: bytes) -> CatalogImport:
    """Liest eine Liste von Objekten mit ``number``, ``name`` und optionalen Feldern."""
    _check_size(data)
    sha = hashlib.sha256(data).hexdigest()
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except RecursionError as exc:
        raise _catalog_error(["JSON-Datei ist zu tief verschachtelt"]) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _catalog_error([f"Keine gültige JSON-Datei: {exc}"]) from exc
    if not isinstance(raw, list):
        raise _catalog_error(["Die JSON-Datei muss eine Liste von Artikeln enthalten"])
    issues: list[CatalogIssue] = []
    articles = []
    for row_no, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            issues.append(CatalogIssue(Severity.ERROR, "Eintrag ist kein Objekt", row_no))
            continue
        articles.append(
            Article(
                number=str(item.get("number", "")).strip(),
                name=str(item.get("name", "")).strip(),
                aliases=tuple(str(a).strip() for a in item.get("aliases", []) if str(a).strip()),
                unit=str(item.get("unit", "")).strip(),
                tax_rate=_decimal(str(item.get("tax_rate") or ""), "Steuersatz", row_no, issues),
                price=_decimal(str(item.get("price") or ""), "Preis", row_no, issues),
                active=bool(item.get("active", True)),
            )
        )
    issues += validate_catalog(articles, first_row=1)
    return CatalogImport(tuple(articles), tuple(issues), sha)


def _row_issues(article: Article, row: int) -> list[CatalogIssue]:
    issues = []
    if not article.number:
        issues.append(CatalogIssue(Severity.ERROR, "Artikelnummer fehlt", row))
    if not article.name:
        issues.append(CatalogIssue(Severity.ERROR, "Bezeichnung fehlt", row))
    if article.tax_rate is not None and not Decimal(0) <= article.tax_rate <= MAX_TAX_RATE:
        issues.append(CatalogIssue(Severity.ERROR, "Steuersatz außerhalb 0–100 %", row))
    if article.price is not None and article.price < 0:
        issues.append(CatalogIssue(Severity.ERROR, "Preis ist negativ", row))
    return issues


def validate_catalog(articles: Sequence[Article], first_row: int = 1) -> list[CatalogIssue]:
    """Prüft Pflichtfelder, Wertebereiche, doppelte Nummern und mehrdeutige Namen."""
    issues: list[CatalogIssue] = []
    numbers: dict[str, list[int]] = defaultdict(list)
    names: dict[str, set[str]] = defaultdict(set)
    for row, article in enumerate(articles, start=first_row):
        issues += _row_issues(article, row)
        if article.number:
            numbers[normalize_number(article.number)].append(row)
        for text in (article.name, *article.aliases):
            if text:
                names[normalize_name(text)].add(article.number)
    for number, rows in sorted(numbers.items()):
        if len(rows) > 1:
            listed = ", ".join(str(r) for r in rows)
            message = f"Artikelnummer {number} mehrfach (Zeilen {listed})"
            issues.append(CatalogIssue(Severity.ERROR, message))
    for name, owners in sorted(names.items()):
        if len(owners) > 1:
            message = (
                f"Bezeichnung/Alias „{name}“ passt zu mehreren Artikeln "
                f"({', '.join(sorted(owners))}); Treffer darüber werden immer zur Prüfung vorgelegt"
            )
            issues.append(CatalogIssue(Severity.WARNING, message))
    return issues


def _catalog_error(problems: Sequence[str]) -> CatalogError:
    shown = list(problems[:MAX_ISSUES_IN_MESSAGE])
    if len(problems) > MAX_ISSUES_IN_MESSAGE:
        shown.append(f"… und {len(problems) - MAX_ISSUES_IN_MESSAGE} weitere")
    return CatalogError(
        "CATALOG_INVALID",
        UserMessage(
            what="Der Artikelkatalog wurde nicht importiert",
            why="; ".join(shown),
            unchanged="Der bisher aktive Katalog bleibt unverändert in Gebrauch",
            action="Bitte die Datei korrigieren oder die Spaltenzuordnung anpassen "
            "und erneut importieren",
        ),
        details=tuple(problems),
    )


class CatalogService:
    """Speichert geprüfte Kataloge als neue Fassung und liefert den aktiven Katalog."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock

    def store(self, profile_id: str, source_name: str, result: CatalogImport) -> int:
        """Legt die Fassung an und aktiviert sie; bei Fehlern wird nichts gespeichert."""
        if result.errors:
            raise _catalog_error([issue.render() for issue in result.errors])
        now = self._clock.now()
        repo = CatalogRepository(self._conn)
        with transaction(self._conn):
            version = repo.add_version(
                profile_id, source_name, result.source_sha256, result.articles, now
            )
            repo.activate(profile_id, version)
            JournalRepository(self._conn).append(
                "catalog",
                profile_id,
                "catalog_activated",
                now,
                {"version": version, "articles": len(result.articles), "source": source_name},
            )
        return version

    def activate(self, profile_id: str, version: int) -> None:
        """Schaltet auf eine frühere Fassung zurück."""
        with transaction(self._conn):
            CatalogRepository(self._conn).activate(profile_id, version)
            JournalRepository(self._conn).append(
                "catalog", profile_id, "catalog_activated", self._clock.now(), {"version": version}
            )

    def active_catalog(self, profile_id: str) -> Catalog | None:
        """Aktiver Katalog des Profils oder ``None``, wenn noch keiner importiert wurde."""
        repo = CatalogRepository(self._conn)
        version = repo.active_version(profile_id)
        return None if version is None else Catalog(repo.articles(version))
