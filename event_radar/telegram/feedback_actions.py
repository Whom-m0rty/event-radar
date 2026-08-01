"""Shared feedback recording for the Telegram channels (buttons + deeplinks).

Kept out of bot.py so it is pure and unit-testable without the async bot runtime.
"""
from __future__ import annotations

import sqlite3

from event_radar.feedback.snapshots import create_snapshot
from event_radar.feedback.sync import record_feedback

# One place mapping a label to its scale. intent = before ("do I want to go"),
# experience = after ("how was it"). Never collapse the two (spec section 7).
LABEL_DIMENSION = {
    "love": "intent", "going": "intent", "meh": "intent", "nope": "intent",
    "great": "experience", "ok": "experience", "bad": "experience", "didnt_go": "experience",
}


def dimension_for(label: str) -> str:
    return LABEL_DIMENSION.get(label, "intent")


def apply_feedback(
    connection: sqlite3.Connection,
    event_id: str,
    user_id: str,
    label: str,
    channel: str,
    affinity: dict[str, float],
    scoring_cfg: dict,
) -> bool:
    """Record one feedback fact with a fresh feature snapshot. Returns False if the
    event is unknown (e.g. a stale deeplink)."""
    event = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return False
    snapshot_id = create_snapshot(connection, dict(event), affinity, scoring_cfg)
    record_feedback(
        connection,
        event_id=event_id,
        user_id=user_id,
        dimension=dimension_for(label),
        label=label,
        channel=channel,
        snapshot_id=snapshot_id,
    )
    return True
