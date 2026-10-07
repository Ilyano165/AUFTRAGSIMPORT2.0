"""Offene Aufträge nach einer Katalogänderung neu zuordnen: erst Vorschau, dann übernehmen."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from ..domain.models import ArticleMatch, MatchStatus, Order
from ..domain.status import OrderStatus
from .matching import ArticleMatcher, Catalog

REMATCHABLE = frozenset({OrderStatus.NEW, OrderStatus.NEEDS_REVIEW, OrderStatus.READY})


def _key(match: ArticleMatch) -> tuple[object, ...]:
    article = match.article
    return (
        match.status,
        match.strategy,
        article.number if article else None,
        tuple(c.article.number for c in match.candidates),
    )


@dataclass(frozen=True, slots=True)
class LineChange:
    """Geänderte Zuordnung einer Position."""

    order_id: str
    company: str
    position: int
    description: str
    before: ArticleMatch
    after: ArticleMatch

    @property
    def kind(self) -> str:
        """Art der Änderung in Worten."""
        was, now = self.before, self.after
        if now.status is MatchStatus.MATCHED and was.status is not MatchStatus.MATCHED:
            return "neu zugeordnet"
        if (
            now.status is MatchStatus.MATCHED
            and was.article
            and now.article
            and was.article.number != now.article.number
        ):
            return "anderer Artikel"
        if now.status is MatchStatus.NEEDS_REVIEW:
            return "Benutzerprüfung erforderlich"
        if now.status is MatchStatus.UNKNOWN and was.article is not None:
            return "Zuordnung entfällt"
        return "Artikeldaten aktualisiert"


@dataclass(frozen=True, slots=True)
class RematchPlan:
    """Vorschau der Neuzuordnung."""

    catalog_version: int | None
    changes: tuple[LineChange, ...]
    checked_orders: int
    checked_lines: int

    @property
    def summary(self) -> str:
        """Kurzfassung."""
        if not self.changes:
            scope = f"{self.checked_lines} Positionen in {self.checked_orders} Aufträgen geprüft"
            return f"{scope} · keine Änderung"
        kinds: dict[str, int] = {}
        for change in self.changes:
            kinds[change.kind] = kinds.get(change.kind, 0) + 1
        parts = " · ".join(f"{n} {k}" for k, n in kinds.items())
        return (
            f"{self.checked_lines} Positionen in {self.checked_orders} Aufträgen geprüft · {parts}"
        )

    @property
    def order_ids(self) -> tuple[str, ...]:
        """Betroffene Aufträge."""
        return tuple(dict.fromkeys(c.order_id for c in self.changes))


def rematch_order(order: Order, catalog: Catalog, version: int | None) -> Order:
    """Auftrag mit neu berechneten Zuordnungen; manuelle Zuordnungen bleiben."""
    matcher = ArticleMatcher(catalog)
    lines = tuple(
        line
        if line.match.status is MatchStatus.MANUAL
        else replace(line, match=replace(matcher.match(line), catalog_version=version))
        for line in order.lines
    )
    return replace(order, lines=lines)


def plan_rematch(
    orders: Sequence[Order], catalog: Catalog, version: int | None, company: Callable[[Order], str]
) -> RematchPlan:
    """Welche Positionen sich ändern würden; Artikeldaten (Preis, Steuer) zählen mit."""
    changes: list[LineChange] = []
    checked_orders = checked_lines = 0
    for order in orders:
        if order.status not in REMATCHABLE:
            continue
        checked_orders += 1
        updated = rematch_order(order, catalog, version)
        for before, after in zip(order.lines, updated.lines, strict=True):
            if before.match.status is MatchStatus.MANUAL:
                continue
            checked_lines += 1
            if (
                _key(before.match) != _key(after.match)
                or before.match.article != after.match.article
            ):
                changes.append(
                    LineChange(
                        order.id,
                        company(order),
                        before.position,
                        before.description,
                        before.match,
                        after.match,
                    )
                )
    return RematchPlan(version, tuple(changes), checked_orders, checked_lines)
