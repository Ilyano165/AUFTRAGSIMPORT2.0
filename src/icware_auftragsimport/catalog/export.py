"""Katalogexport: XLSX (empfohlen), CSV für Excel mit Formelschutz, JSON zum Wiedereinlesen."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..domain.models import Article
from .table import FORMULA_PREFIXES, JSON_FORMAT
from .xlsx import write_xlsx

HEADERS = ("Artikelnummer", "Bezeichnung", "Alias", "Preis", "Steuersatz", "Einheit", "Aktiv")
JSON_VERSION = 1


class ExportFormat(StrEnum):
    """Exportformate mit Dateiendung."""

    XLSX = "xlsx"
    CSV = "csv"
    JSON = "json"


def neutralize(text: str) -> str:
    """Schutz vor Formel-Injection (OWASP): Hochkomma vor Formelzeichen am Zellanfang."""
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _decimal_de(value: Decimal | None) -> str:
    return "" if value is None else f"{value:f}".replace(".", ",")


def to_csv(articles: Sequence[Article]) -> bytes:
    """CSV für Excel: UTF-8 mit BOM, Semikolon, deutsche Dezimalzahlen."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(HEADERS)
    for a in articles:
        writer.writerow(
            [
                neutralize(a.number),
                neutralize(a.name),
                neutralize("|".join(a.aliases)),
                _decimal_de(a.price),
                _decimal_de(a.tax_rate),
                neutralize(a.unit),
                "ja" if a.active else "nein",
            ]
        )
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def to_xlsx(articles: Sequence[Article]) -> bytes:
    """Arbeitsmappe; Texte sind Texte, Excel führt darin keine Formeln aus."""
    rows = [
        [
            a.number,
            a.name,
            "|".join(a.aliases),
            a.price,
            a.tax_rate,
            a.unit,
            "ja" if a.active else "nein",
        ]
        for a in articles
    ]
    return write_xlsx("Artikel", HEADERS, rows)


def catalog_document(
    articles: Sequence[Article],
    *,
    profile_id: str,
    profile_name: str,
    version: int | None,
    created: datetime,
    source: str = "",
) -> dict[str, object]:
    """JSON-Dokument für Export und Backup; wird vom Import wieder gelesen."""
    return {
        "format": JSON_FORMAT,
        "format_version": JSON_VERSION,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "catalog_version": version,
        "created": created.isoformat(),
        "source": source,
        "articles": [
            {
                "number": a.number,
                "name": a.name,
                "aliases": list(a.aliases),
                "price": None if a.price is None else f"{a.price:f}".replace(".", ","),
                "tax_rate": None if a.tax_rate is None else f"{a.tax_rate:f}".replace(".", ","),
                "unit": a.unit,
                "active": a.active,
            }
            for a in articles
        ],
    }


def to_json(articles: Sequence[Article], **meta: object) -> bytes:
    """JSON mit Metadaten."""
    document = catalog_document(articles, **meta)  # type: ignore[arg-type]
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
