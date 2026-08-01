"""Persistence helpers for events. Kept separate from the source so any source
writes through the same idempotent path."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from event_radar.models import RawEvent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_events(connection: sqlite3.Connection, events: list[RawEvent]) -> tuple[int, int]:
    """Insert new events, refresh existing ones. Returns (inserted, updated).

    Idempotent: re-running fetch never duplicates. first_seen_at is preserved on
    the first insert; last_seen_at moves forward every time we see the event.
    """
    inserted = 0
    updated = 0
    now = _now_iso()

    for event in events:
        existing = connection.execute(
            "SELECT id FROM events WHERE id = ?", (event.id,)
        ).fetchone()
        lineup_json = json.dumps(event.lineup, ensure_ascii=False)

        if existing is None:
            connection.execute(
                "INSERT INTO events (id, ra_id, title, venue_name, city, starts_at, "
                "ends_at, price_min, is_free, url, description_raw, lineup_raw, "
                "is_open_air, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id, event.ra_id, event.title, event.venue_name, event.city,
                    event.starts_at, event.ends_at, event.price_min,
                    _as_int_bool(event.is_free), event.url, event.description_raw,
                    lineup_json, _as_int_bool(event.is_open_air), now, now,
                ),
            )
            inserted += 1
        else:
            connection.execute(
                "UPDATE events SET title=?, venue_name=?, city=?, starts_at=?, "
                "ends_at=?, price_min=?, is_free=?, url=?, description_raw=?, "
                "lineup_raw=?, is_open_air=?, last_seen_at=? WHERE id=?",
                (
                    event.title, event.venue_name, event.city, event.starts_at,
                    event.ends_at, event.price_min, _as_int_bool(event.is_free),
                    event.url, event.description_raw, lineup_json,
                    _as_int_bool(event.is_open_air), now, event.id,
                ),
            )
            updated += 1

    connection.commit()
    return inserted, updated


def _as_int_bool(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0
