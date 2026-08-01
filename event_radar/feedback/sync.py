"""Turn calendar colours into feedback (spec 8.1).

Append-only: a colour change writes a NEW feedback_events row with a fresh
timestamp; the old one is never touched. The actual opinion is the latest row
per (event_id, user_id, dimension) — a changed mind over time is itself signal.

Idempotent: sync.last_color_id remembers the colour we last saw, so re-running
with no recolour writes nothing. Only a *change* records feedback.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from event_radar.feedback.snapshots import create_snapshot

logger = logging.getLogger(__name__)


def record_feedback(
    connection: sqlite3.Connection,
    event_id: str,
    user_id: str,
    dimension: str,
    label: str,
    channel: str,
    snapshot_id: int | None = None,
    note: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO feedback_events "
        "(event_id, user_id, dimension, label, channel, created_at, note, snapshot_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (event_id, user_id, dimension, label, channel,
         datetime.now(timezone.utc).isoformat(), note, snapshot_id),
    )
    connection.commit()


def sync_calendar_colors(
    connection: sqlite3.Connection,
    calendar_events: list[dict],
    affinity: dict[str, float],
    config: dict,
) -> int:
    """Compare each calendar event's colour with the last seen one; record changes.

    `calendar_events` is the raw list from the Calendar API: [{id, colorId, ...}].
    Returns the number of feedback rows written this run.
    """
    color_map = config.get("calendar", {}).get("color_feedback", {})
    scoring_cfg = config.get("scoring", {})

    # Map the Google event id back to our event id, plus the last colour we saw.
    sync_rows = {}
    for row in connection.execute(
        "SELECT event_id, gcal_event_id, last_color_id FROM sync WHERE gcal_event_id IS NOT NULL"
    ):
        sync_rows[row["gcal_event_id"]] = (row["event_id"], row["last_color_id"])

    written = 0
    for calendar_event in calendar_events:
        gcal_id = calendar_event.get("id")
        mapping = sync_rows.get(gcal_id)
        if mapping is None:
            continue
        event_id, last_color = mapping
        current_color = calendar_event.get("colorId")  # None if default/uncoloured

        if current_color == last_color:
            continue  # no change since last sync

        # Remember the new colour regardless of whether it maps to a label.
        connection.execute(
            "UPDATE sync SET last_color_id = ? WHERE event_id = ?", (current_color, event_id)
        )
        connection.commit()

        if current_color in color_map:
            meta = color_map[current_color]
            event = connection.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
            snapshot_id = create_snapshot(connection, dict(event), affinity, scoring_cfg)
            record_feedback(
                connection,
                event_id=event_id,
                user_id="owner",
                dimension=meta["dimension"],
                label=meta["label"],
                channel="gcal_color",
                snapshot_id=snapshot_id,
            )
            written += 1
            logger.info("Feedback: %s -> %s/%s (colour %s)", event_id, meta["dimension"], meta["label"], current_color)

    return written


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
