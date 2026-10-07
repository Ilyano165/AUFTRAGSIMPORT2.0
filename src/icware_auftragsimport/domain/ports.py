"""Schnittstellen, die die Domäne von außen benötigt."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Zeitquelle; in Tests durch eine feste Uhr ersetzbar."""

    def now(self) -> datetime:
        """Aktueller Zeitpunkt, zeitzonenbewusst in UTC."""
        ...
