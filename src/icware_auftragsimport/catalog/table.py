"""Katalogquelle als einheitliche Tabelle: CSV/TXT, XLSX oder JSON."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from enum import StrEnum

from ..security.fs import file_extension
from ..security.limits import FILES
from .xlsx import XlsxError, read_xlsx

MAX_COLUMNS = 200
DELIMITERS = (";", ",", "\t", "|")
DELIMITER_LABELS = {";": "Semikolon", ",": "Komma", "\t": "Tabulator", "|": "senkrechter Strich"}
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
JSON_FORMAT = "icware-artikelkatalog"


class SourceKind(StrEnum):
    """Dateiart einer Katalogquelle."""

    CSV = "csv"
    XLSX = "xlsx"
    JSON = "json"

    @property
    def label(self) -> str:
        """Bezeichnung für die Oberfläche."""
        return {"csv": "CSV/Text", "xlsx": "Excel (XLSX)", "json": "JSON"}[self.value]


class CatalogSourceError(ValueError):
    """Die Datei lässt sich nicht als Tabelle lesen; ``str(exc)`` ist für Benutzer formuliert."""


@dataclass(frozen=True, slots=True)
class TableOptions:
    """Leseoptionen; ``None`` heißt automatisch erkennen."""

    has_header: bool = True
    delimiter: str | None = None
    sheet: str | None = None


@dataclass(frozen=True, slots=True)
class RawTable:
    """Gelesene Quelle: Spaltenköpfe und Zeilen mit Zeilennummer der Quelle."""

    source_name: str
    kind: SourceKind
    sha256: str
    headers: tuple[str, ...]
    rows: tuple[tuple[int, tuple[str, ...]], ...]
    encoding: str = ""
    delimiter: str = ""
    sheets: tuple[str, ...] = ()
    sheet: str = ""
    has_header: bool = True

    @property
    def description(self) -> str:
        """Kurzbeschreibung, etwa „CSV/Text · Windows-1252 · Semikolon · 128 Zeilen“."""
        parts = [self.kind.label]
        if self.encoding:
            parts.append(self.encoding)
        if self.delimiter:
            parts.append(DELIMITER_LABELS.get(self.delimiter, self.delimiter))
        if self.sheet:
            parts.append(f"Blatt „{self.sheet}“")
        parts.append(f"{len(self.rows)} Datenzeilen")
        return " · ".join(parts)


def detect_kind(name: str, data: bytes) -> SourceKind:
    """Dateiart aus Endung, sonst aus dem Inhalt."""
    ext = file_extension(name).lower().lstrip(".")
    if ext in ("csv", "txt", "tsv"):
        return SourceKind.CSV
    if ext == "xlsx":
        return SourceKind.XLSX
    if ext == "json":
        return SourceKind.JSON
    if ext in ("xls", "xlsm", "ods"):
        raise CatalogSourceError(
            f"Dateityp {ext} wird nicht unterstützt; bitte als .xlsx oder .csv speichern"
        )
    head = data[:4].lstrip(b"\xef\xbb\xbf")
    if head.startswith(b"PK"):
        return SourceKind.XLSX
    if head[:1] in (b"[", b"{"):
        return SourceKind.JSON
    return SourceKind.CSV


def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "UTF-8"
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "Windows-1252 (ANSI)"


def _sniff_delimiter(text: str) -> str:
    lines = [line for line in text.splitlines()[:20] if line.strip()]
    if not lines:
        return ";"
    best, best_score = ";", -1
    for delimiter in DELIMITERS:
        counts = [line.count(delimiter) for line in lines]
        if not counts[0]:
            continue
        consistent = sum(1 for c in counts if c == counts[0])
        score = consistent * 1000 + counts[0]
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _unprotect(cell: str) -> str:
    """Entfernt das Schutz-Hochkomma, das der eigene CSV-Export vor Formelzeichen setzt."""
    return (
        cell[1:]
        if cell.startswith("'") and cell[1:2] and cell[1:].startswith(FORMULA_PREFIXES)
        else cell
    )


def _headers(first: list[str], width: int, has_header: bool) -> tuple[str, ...]:
    if not has_header:
        return tuple(f"Spalte {i + 1}" for i in range(width))
    names: list[str] = []
    for index in range(width):
        name = (first[index].strip() if index < len(first) else "") or f"Spalte {index + 1}"
        candidate, counter = name, 2
        while candidate in names:
            candidate, counter = f"{name} ({counter})", counter + 1
        names.append(candidate)
    return tuple(names)


def _finish(
    rows: list[tuple[int, list[str]]], has_header: bool
) -> tuple[tuple[str, ...], tuple[tuple[int, tuple[str, ...]], ...]]:
    rows = [(n, [c.strip() for c in r]) for n, r in rows]
    if not rows:
        raise CatalogSourceError("Die Datei enthält keine Zeilen")
    width = max(len(r) for _, r in rows)
    if width > MAX_COLUMNS:
        raise CatalogSourceError(f"Mehr als {MAX_COLUMNS} Spalten")
    headers = _headers(rows[0][1], width, has_header)
    body = rows[1:] if has_header else rows
    data = tuple((n, tuple(r + [""] * (width - len(r)))) for n, r in body if any(r))
    return headers, data


def _read_csv(
    data: bytes, options: TableOptions
) -> tuple[tuple[str, ...], tuple[tuple[int, tuple[str, ...]], ...], str, str]:
    text, encoding = _decode(data)
    delimiter = options.delimiter or _sniff_delimiter(text)
    previous = csv.field_size_limit(FILES.catalog_max_field_chars)
    rows: list[tuple[int, list[str]]] = []
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        for row in reader:
            if len(rows) > FILES.catalog_max_rows:
                raise CatalogSourceError(f"Mehr als {FILES.catalog_max_rows} Zeilen")
            rows.append((reader.line_num, [_unprotect(c) for c in row]))
    except csv.Error as exc:
        raise CatalogSourceError(f"CSV fehlerhaft oder Feld zu lang ({exc})") from exc
    finally:
        csv.field_size_limit(previous)
    headers, body = _finish(rows, options.has_header)
    return headers, body, encoding, delimiter


def _json_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, (int, float)):
        return str(value).replace(".", ",")
    if isinstance(value, list):
        return "|".join(_json_value(v) for v in value)
    if isinstance(value, dict):
        raise CatalogSourceError("Verschachtelte Objekte in Artikeln werden nicht unterstützt")
    return str(value)


def _read_json(data: bytes) -> tuple[tuple[str, ...], tuple[tuple[int, tuple[str, ...]], ...]]:
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except RecursionError as exc:
        raise CatalogSourceError("JSON-Datei ist zu tief verschachtelt") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogSourceError(f"Keine gültige JSON-Datei ({exc.__class__.__name__})") from exc
    items = raw.get("articles") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise CatalogSourceError(
            "Erwartet wird eine Liste von Artikeln oder ein Objekt mit „articles“"
        )
    if len(items) > FILES.catalog_max_rows:
        raise CatalogSourceError(f"Mehr als {FILES.catalog_max_rows} Artikel")
    keys: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise CatalogSourceError("Jeder Artikel muss ein Objekt sein")
        keys += [str(k) for k in item if str(k) not in keys]
    if len(keys) > MAX_COLUMNS:
        raise CatalogSourceError(f"Mehr als {MAX_COLUMNS} Felder")
    rows = tuple(
        (index, tuple(_json_value(item.get(key))[: FILES.catalog_max_field_chars] for key in keys))
        for index, item in enumerate(items, start=1)
    )
    return tuple(keys), tuple(r for r in rows if any(r[1]))


def read_table(data: bytes, name: str, options: TableOptions = TableOptions()) -> RawTable:  # noqa: B008
    """Liest eine Katalogquelle; ``CatalogSourceError`` mit verständlicher Meldung bei Problemen."""
    if not data:
        raise CatalogSourceError("Die Datei ist leer")
    if len(data) > FILES.catalog_max_bytes:
        raise CatalogSourceError(f"Datei größer als {FILES.catalog_max_bytes // (1024 * 1024)} MB")
    kind = detect_kind(name, data)
    sha = hashlib.sha256(data).hexdigest()
    if kind is SourceKind.CSV:
        headers, rows, encoding, delimiter = _read_csv(data, options)
        return RawTable(
            name, kind, sha, headers, rows, encoding, delimiter, has_header=options.has_header
        )
    if kind is SourceKind.XLSX:
        try:
            sheets, sheet, raw_rows = read_xlsx(data, options.sheet)
        except XlsxError as exc:
            raise CatalogSourceError(str(exc)) from exc
        headers, rows = _finish(raw_rows, options.has_header)
        return RawTable(
            name,
            kind,
            sha,
            headers,
            rows,
            sheets=tuple(sheets),
            sheet=sheet,
            has_header=options.has_header,
        )
    headers, rows = _read_json(data)
    return RawTable(name, kind, sha, headers, rows, "UTF-8")
