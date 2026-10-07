"""Änderungsstatistik zwischen zwei Katalogfassungen."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..domain.models import Article
from ..services.matching import normalize_number

MAX_STORED_DETAILS = 500
FIELD_NAMES = {
    "name": "Name",
    "aliases": "Alias",
    "price": "Preis",
    "tax_rate": "Steuersatz",
    "unit": "Einheit",
    "active": "Status",
}


@dataclass(frozen=True, slots=True)
class ArticleChange:
    """Änderung eines Artikels."""

    number: str
    kind: str
    fields: tuple[str, ...] = ()
    before: str = ""
    after: str = ""


@dataclass(frozen=True, slots=True)
class CatalogChanges:
    """Neu, entfernt, geändert und unverändert gegenüber der bisher aktiven Fassung."""

    added: int = 0
    removed: int = 0
    changed: int = 0
    unchanged: int = 0
    price_changes: int = 0
    details: tuple[ArticleChange, ...] = ()
    first_import: bool = False

    @property
    def summary(self) -> str:
        """Kurzform, etwa „+12 neu · −3 entfernt · 25 geändert (18 Preise) · 140 unverändert“."""
        if self.first_import:
            return f"Erstimport: {self.added} Artikel"
        changed = f"{self.changed} geändert" + (
            f" ({self.price_changes} Preise)" if self.price_changes else ""
        )
        return f"+{self.added} neu · −{self.removed} entfernt · {changed} · {self.unchanged} gleich"

    def to_json(self) -> dict[str, object]:
        """Speicherbare Form; Details auf ``MAX_STORED_DETAILS`` begrenzt."""
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "price_changes": self.price_changes,
            "first_import": self.first_import,
            "details": [
                {
                    "number": d.number,
                    "kind": d.kind,
                    "fields": list(d.fields),
                    "before": d.before,
                    "after": d.after,
                }
                for d in self.details[:MAX_STORED_DETAILS]
            ],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> CatalogChanges:
        """Gegenstück zu :meth:`to_json`; fehlende Werte gelten als 0."""

        def number(key: str) -> int:
            value = data.get(key, 0)
            return (
                int(value) if isinstance(value, (int, float, str)) and str(value).isdigit() else 0
            )

        raw = data.get("details", [])
        details = tuple(
            ArticleChange(
                str(d.get("number", "")),
                str(d.get("kind", "")),
                tuple(d.get("fields", [])),
                str(d.get("before", "")),
                str(d.get("after", "")),
            )
            for d in (raw if isinstance(raw, list) else [])
            if isinstance(d, dict)
        )
        return cls(
            number("added"),
            number("removed"),
            number("changed"),
            number("unchanged"),
            number("price_changes"),
            details,
            bool(data.get("first_import")),
        )


def _price(article: Article) -> str:
    """Preis mit mindestens zwei Nachkommastellen, etwa ``6,90``."""
    price = article.price
    if price is None:
        return "–"
    exponent = price.normalize().as_tuple().exponent
    decimals = max(2, -exponent if isinstance(exponent, int) else 2)
    return f"{price:.{decimals}f}".replace(".", ",")


def compare(old: Sequence[Article] | None, new: Sequence[Article]) -> CatalogChanges:
    """Vergleicht über die normalisierte Artikelnummer."""
    if old is None:
        return CatalogChanges(added=len(new), first_import=True)
    before = {normalize_number(a.number): a for a in old}
    after = {normalize_number(a.number): a for a in new}
    details: list[ArticleChange] = []
    changed = unchanged = prices = 0
    for key, article in after.items():
        previous = before.get(key)
        if previous is None:
            details.append(ArticleChange(article.number, "neu", after=article.name))
            continue
        fields = tuple(
            name for name in FIELD_NAMES if getattr(previous, name) != getattr(article, name)
        )
        if not fields:
            unchanged += 1
            continue
        changed += 1
        if "price" in fields:
            prices += 1
        before_text = _price(previous) if fields == ("price",) else ""
        after_text = _price(article) if fields == ("price",) else ""
        details.append(
            ArticleChange(
                article.number,
                "geändert",
                tuple(FIELD_NAMES[f] for f in fields),
                before_text,
                after_text,
            )
        )
    removed = [a for key, a in before.items() if key not in after]
    details += [ArticleChange(a.number, "entfernt", before=a.name) for a in removed]
    added = sum(1 for d in details if d.kind == "neu")
    return CatalogChanges(added, len(removed), changed, unchanged, prices, tuple(details))
