"""Interner Belegnummernkreis; ersetzt die zeitstempelbasierten Nummern aus 1.1.0."""

from __future__ import annotations

from ..infrastructure.repositories import CounterRepository

NUMBER_WIDTH = 6


def counter_name(prefix: str, year: int) -> str:
    """Zählername je Präfix und Jahr."""
    return f"document:{prefix}:{year}"


def profile_counter_name(profile_id: str, prefix: str, year: int) -> str:
    """Zählername je Profil, Präfix und Jahr: jede Firma hat ihren eigenen Nummernkreis."""
    return f"document:{profile_id}:{prefix}:{year}"


def next_document_number(
    counters: CounterRepository, prefix: str, year: int, profile_id: str = ""
) -> str:
    """Nächste Belegnummer, etwa ``AI-2026-000001``; nur innerhalb einer Transaktion.

    Mit ``profile_id`` zählt jedes Profil getrennt. Der Profilzähler startet nie unter dem
    gemeinsamen Zähler früherer Versionen, damit keine bereits vergebene Nummer erneut entsteht.
    """
    name = counter_name(prefix, year)
    if profile_id:
        legacy = counters.peek(name)
        name = profile_counter_name(profile_id, prefix, year)
        counters.raise_to(name, legacy)
    value = counters.next(name)
    return f"{prefix}-{year}-{value:0{NUMBER_WIDTH}d}"
