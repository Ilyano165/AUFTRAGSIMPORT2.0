"""Artikelzuordnung (Pipeline-Schritt 12).

Reihenfolge: explizite Artikelnummer, exakter Alias, exakter Name, normalisierter Name,
kontrollierter Fuzzy-Abgleich. Ein Fuzzy-Treffer wählt nie still einen Artikel aus,
sondern legt Kandidaten zur Bestätigung vor. Mehrdeutigkeit führt immer zur Prüfung.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from difflib import SequenceMatcher

from ..domain.models import (
    Article,
    ArticleMatch,
    MatchCandidate,
    MatchStatus,
    MatchStrategy,
    OrderLine,
)
from ..domain.provenance import Confidence

FUZZY_MIN_SCORE = 0.80
FUZZY_MAX_CANDIDATES = 5
MIN_TOKEN_LENGTH = 3
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_WORD = re.compile(r"[^0-9a-z]+")
_NON_ALNUM = re.compile(r"[^0-9A-Z]")


def normalize_number(value: str) -> str:
    """Artikelnummer ohne Trennzeichen und Leerraum, in Großbuchstaben."""
    return _NON_ALNUM.sub("", value.upper())


def normalize_name(value: str) -> str:
    """Name ohne Groß-/Kleinschreibung, Umlaute, Akzente und Satzzeichen."""
    folded = value.casefold().translate(_UMLAUTS)
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(_NON_WORD.sub(" ", ascii_only).split())


def _exact(value: str) -> str:
    return " ".join(value.casefold().split())


class Catalog:
    """Unveränderlicher Katalog mit Indizes je Zuordnungsregel; nur aktive Artikel."""

    def __init__(self, articles: Iterable[Article]) -> None:
        self.articles = tuple(a for a in articles if a.active)
        self._by_number: dict[str, list[Article]] = defaultdict(list)
        self._by_alias: dict[str, list[Article]] = defaultdict(list)
        self._by_name: dict[str, list[Article]] = defaultdict(list)
        self._by_normalized: dict[str, list[Article]] = defaultdict(list)
        self._by_token: dict[str, set[int]] = defaultdict(set)
        for index, article in enumerate(self.articles):
            self._by_number[normalize_number(article.number)].append(article)
            self._by_name[_exact(article.name)].append(article)
            self._by_normalized[normalize_name(article.name)].append(article)
            for alias in article.aliases:
                self._by_alias[_exact(alias)].append(article)
                self._by_normalized[normalize_name(alias)].append(article)
            for token in normalize_name(" ".join((article.name, *article.aliases))).split():
                if len(token) >= MIN_TOKEN_LENGTH:
                    self._by_token[token].add(index)

    def by_number(self, number: str) -> list[Article]:
        """Artikel zur normalisierten Nummer."""
        return list(self._by_number.get(normalize_number(number), ()))

    def by_alias(self, text: str) -> list[Article]:
        """Artikel mit exakt diesem Alias."""
        return _unique(self._by_alias.get(_exact(text), ()))

    def by_name(self, text: str) -> list[Article]:
        """Artikel mit exakt diesem Namen."""
        return _unique(self._by_name.get(_exact(text), ()))

    def by_normalized_name(self, text: str) -> list[Article]:
        """Artikel, deren Name oder Alias normalisiert gleich ist."""
        return _unique(self._by_normalized.get(normalize_name(text), ()))

    def knows_name(self, text: str) -> bool:
        """True, wenn genau ein Artikel exakt oder normalisiert passt."""
        hits = self.by_alias(text) or self.by_name(text) or self.by_normalized_name(text)
        return len(hits) == 1

    def fuzzy(self, text: str) -> list[MatchCandidate]:
        """Ähnliche Artikel ab ``FUZZY_MIN_SCORE``, beste zuerst, deterministisch sortiert."""
        wanted = normalize_name(text)
        indices: set[int] = set()
        for token in wanted.split():
            indices |= self._by_token.get(token, set())
        scored = []
        for index in indices:
            article = self.articles[index]
            names = (article.name, *article.aliases)
            score = max(SequenceMatcher(None, wanted, normalize_name(n)).ratio() for n in names)
            if score >= FUZZY_MIN_SCORE:
                scored.append(MatchCandidate(article, MatchStrategy.FUZZY, round(score, 3)))
        scored.sort(key=lambda c: (-c.score, c.article.number))
        return scored[:FUZZY_MAX_CANDIDATES]


def _unique(articles: Sequence[Article]) -> list[Article]:
    seen: dict[str, Article] = {}
    for article in articles:
        seen.setdefault(article.number, article)
    return list(seen.values())


def _candidates(articles: Sequence[Article], strategy: MatchStrategy) -> tuple[MatchCandidate, ...]:
    return tuple(MatchCandidate(a, strategy, 1.0) for a in sorted(articles, key=lambda a: a.number))


class ArticleMatcher:
    """Ordnet Positionen Katalogartikeln zu, ohne je zu raten."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def match_line(self, line: OrderLine) -> OrderLine:
        """Position mit Zuordnung; manuelle Zuordnungen bleiben unangetastet."""
        if line.match.status is MatchStatus.MANUAL:
            return line
        return replace(line, match=self.match(line))

    def match(self, line: OrderLine) -> ArticleMatch:
        """Wendet die Regeln in fester Reihenfolge an."""
        hint = line.article_hint
        if hint.value:
            explicit = hint.evidence is not None and hint.evidence.confidence is Confidence.CERTAIN
            result = self._by_number(hint.value, explicit, line.description)
            if result is not None:
                return result
        return self._by_description(line.description)

    def _by_number(self, number: str, explicit: bool, description: str) -> ArticleMatch | None:
        hits = self._catalog.by_number(number)
        if len(hits) == 1:
            return ArticleMatch(
                MatchStatus.MATCHED,
                hits[0],
                MatchStrategy.EXPLICIT_NUMBER,
                f"Artikelnummer {number} im Katalog gefunden",
            )
        if len(hits) > 1:
            return ArticleMatch(
                MatchStatus.NEEDS_REVIEW,
                None,
                MatchStrategy.EXPLICIT_NUMBER,
                f"Artikelnummer {number} ist im Katalog mehrdeutig",
                _candidates(hits, MatchStrategy.EXPLICIT_NUMBER),
            )
        if not explicit:
            return None
        suggestion = self._by_description(description)
        candidates = suggestion.candidates
        if not candidates and suggestion.article is not None:
            candidates = (MatchCandidate(suggestion.article, suggestion.strategy, 1.0),)
        reason = f"Artikelnummer {number} steht nicht im Katalog"
        if candidates:
            reason += "; Artikel mit passender Bezeichnung vorgeschlagen"
        status = MatchStatus.NEEDS_REVIEW if candidates else MatchStatus.UNKNOWN
        return ArticleMatch(status, None, MatchStrategy.EXPLICIT_NUMBER, reason, candidates)

    def _by_description(self, text: str) -> ArticleMatch:
        if not text.strip():
            return ArticleMatch(MatchStatus.UNKNOWN, reason="Keine Artikelbezeichnung vorhanden")
        for strategy, finder in (
            (MatchStrategy.ALIAS, self._catalog.by_alias),
            (MatchStrategy.NAME, self._catalog.by_name),
            (MatchStrategy.NORMALIZED_NAME, self._catalog.by_normalized_name),
        ):
            hits = finder(text)
            if len(hits) == 1:
                return ArticleMatch(
                    MatchStatus.MATCHED,
                    hits[0],
                    strategy,
                    f"Eindeutiger Treffer über {strategy.label}",
                )
            if len(hits) > 1:
                return ArticleMatch(
                    MatchStatus.NEEDS_REVIEW,
                    None,
                    strategy,
                    f"Mehrere Artikel passen über {strategy.label}",
                    _candidates(hits, strategy),
                )
        fuzzy = self._catalog.fuzzy(text)
        if fuzzy:
            return ArticleMatch(
                MatchStatus.NEEDS_REVIEW,
                None,
                MatchStrategy.FUZZY,
                "Nur ähnliche Artikel gefunden; bitte Artikel bestätigen",
                tuple(fuzzy),
            )
        return ArticleMatch(MatchStatus.UNKNOWN, reason="Kein passender Artikel im Katalog")
