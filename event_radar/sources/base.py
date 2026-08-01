"""Source protocol. New sources drop in beside ra.py with the same signature."""
from __future__ import annotations

from typing import Protocol

from event_radar.models import RawEvent


class Source(Protocol):
    name: str

    def fetch(self, city: str, date_from: str, date_to: str) -> list[RawEvent]:
        """Return events for a city within [date_from, date_to] (ISO dates).

        Must degrade gracefully: an unparseable item is skipped with a log, the
        batch never crashes (spec section 2).
        """
        ...
