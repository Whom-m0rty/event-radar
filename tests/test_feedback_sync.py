"""Tests for colour->feedback sync: append-only + idempotent (spec section 7)."""
import json

import pytest

from event_radar import db
from event_radar.feedback.sync import sync_calendar_colors

CONFIG = {
    "calendar": {"color_feedback": {
        "10": {"dimension": "intent", "label": "love"},
        "11": {"dimension": "intent", "label": "nope"},
    }},
    "scoring": {
        "weights": {"base": 18, "music": 40, "bonus_free": 15, "bonus_openair": 12,
                    "bonus_cheap": 8, "penalty_price": 0.4, "bonus_weekend": 10, "bonus_venue": 15},
        "music": {"max_weight": 0.7, "mean_weight": 0.3},
        "price": {"cheap_threshold_eur": 15, "penalty_above_eur": 25},
        "weekend_days": [4, 5], "venue_whitelist": [],
    },
}


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(str(tmp_path / "t.db"))
    connection.execute(
        "INSERT INTO events (id, title, lineup_raw, is_free, is_open_air, starts_at) "
        "VALUES ('ra:1', 'Ev', ?, 1, 1, '2026-08-01T23:00:00.000')",
        (json.dumps(["Mau P"]),),
    )
    connection.execute(
        "INSERT INTO sync (event_id, gcal_event_id, last_color_id) VALUES ('ra:1', 'gc1', NULL)"
    )
    connection.commit()
    return connection


def _count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM feedback_events").fetchone()["n"]


def test_colour_records_feedback_and_snapshot(conn):
    written = sync_calendar_colors(conn, [{"id": "gc1", "colorId": "10"}], {"mau p": 0.6}, CONFIG)
    assert written == 1
    row = conn.execute("SELECT * FROM feedback_events").fetchone()
    assert row["label"] == "love" and row["channel"] == "gcal_color"
    assert row["snapshot_id"] is not None
    snap = conn.execute("SELECT * FROM feature_snapshots WHERE id = ?", (row["snapshot_id"],)).fetchone()
    assert snap["profile_json"] == '{"mau p": 0.6}'


def test_idempotent_no_duplicate_on_same_colour(conn):
    sync_calendar_colors(conn, [{"id": "gc1", "colorId": "10"}], {}, CONFIG)
    sync_calendar_colors(conn, [{"id": "gc1", "colorId": "10"}], {}, CONFIG)
    assert _count(conn) == 1  # second run sees no change


def test_mind_change_appends_new_row(conn):
    sync_calendar_colors(conn, [{"id": "gc1", "colorId": "10"}], {}, CONFIG)   # love
    sync_calendar_colors(conn, [{"id": "gc1", "colorId": "11"}], {}, CONFIG)   # -> nope
    assert _count(conn) == 2  # both kept, append-only
    labels = [r["label"] for r in conn.execute("SELECT label FROM feedback_events ORDER BY id")]
    assert labels == ["love", "nope"]


def test_default_colour_no_label(conn):
    # Recolour to a non-mapped colour (e.g. remove) writes nothing but updates state.
    written = sync_calendar_colors(conn, [{"id": "gc1", "colorId": "7"}], {}, CONFIG)
    assert written == 0
