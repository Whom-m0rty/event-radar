"""Tests for the Telegram feedback recording (pure, no bot runtime)."""
import json

from event_radar import db
from event_radar.telegram.feedback_actions import apply_feedback, dimension_for

SCORING = {
    "weights": {"base": 18, "music": 40, "bonus_free": 15, "bonus_openair": 12,
                "bonus_cheap": 8, "penalty_price": 0.4, "bonus_weekend": 10, "bonus_venue": 15},
    "music": {"max_weight": 0.7, "mean_weight": 0.3},
    "price": {"cheap_threshold_eur": 15, "penalty_above_eur": 25},
    "weekend_days": [4, 5], "venue_whitelist": [],
}


def test_dimension_mapping():
    assert dimension_for("love") == "intent"
    assert dimension_for("going") == "intent"
    assert dimension_for("great") == "experience"
    assert dimension_for("didnt_go") == "experience"


def test_apply_feedback_records_with_snapshot(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO events (id, title, lineup_raw, starts_at) VALUES ('ra:1','Ev',?, '2026-08-01T23:00:00.000')",
        (json.dumps(["Mau P"]),),
    )
    conn.commit()

    ok = apply_feedback(conn, "ra:1", "tg-42", "love", "tg_button", {"mau p": 0.6}, SCORING)
    assert ok is True
    row = conn.execute("SELECT * FROM feedback_events").fetchone()
    assert row["dimension"] == "intent" and row["label"] == "love"
    assert row["channel"] == "tg_button" and row["user_id"] == "tg-42"
    assert row["snapshot_id"] is not None


def test_apply_feedback_unknown_event(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    assert apply_feedback(conn, "ra:missing", "u", "love", "deeplink", {}, SCORING) is False
