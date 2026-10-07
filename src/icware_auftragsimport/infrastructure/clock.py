"""Zeitquellen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    """Echte Uhr in UTC."""

    def now(self) -> datetime:
        """Aktueller Zeitpunkt in UTC."""
        return datetime.now(UTC)


class FixedClock:
    """Steuerbare Uhr für Tests und reproduzierbare Läufe."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock braucht einen zeitzonenbewussten Startzeitpunkt")
        self._now = start

    def now(self) -> datetime:
        """Der zuletzt gesetzte Zeitpunkt."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Stellt die Uhr vor."""
        self._now += timedelta(seconds=seconds)
