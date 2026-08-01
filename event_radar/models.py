"""Data shapes passed between layers. Plain dataclasses, no ORM."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RawEvent:
    """One event as pulled from a source, before scoring.

    Field names match the `events` table so persistence is a direct mapping.
    A source fills what it can; unknown fields stay None. For RA specifically,
    the listing query has no price/description — those come from a later detail
    pass, so they are None here for now.
    """

    ra_id: str
    title: str | None = None
    venue_name: str | None = None
    city: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    price_min: float | None = None
    is_free: bool | None = None
    url: str | None = None
    description_raw: str | None = None
    lineup: list[str] = field(default_factory=list)
    is_open_air: bool | None = None

    @property
    def id(self) -> str:
        """Global id used as the primary key: prefix keeps sources disjoint."""
        return f"ra:{self.ra_id}"
