"""Test that the feedback export is self-contained (snapshot glued in)."""
import json

from event_radar import db
from event_radar.export.dump import export_feedback, export_impressions


def test_feedback_export_glues_snapshot(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO events (id, title, lineup_raw) VALUES ('ra:1', 'Ev', ?)",
        (json.dumps(["Mau P"]),),
    )
    conn.execute(
        "INSERT INTO feature_snapshots (event_id, features_json, score, breakdown_json, "
        "profile_hash, profile_json, created_at) VALUES ('ra:1', ?, 58.0, ?, 'h', ?, 't')",
        (json.dumps({"music_max": 0.6}), json.dumps({"base": 18}), json.dumps({"mau p": 0.6})),
    )
    snapshot_id = conn.execute("SELECT id FROM feature_snapshots").fetchone()["id"]
    conn.execute(
        "INSERT INTO feedback_events (event_id, user_id, dimension, label, channel, created_at, snapshot_id) "
        "VALUES ('ra:1', 'owner', 'intent', 'love', 'gcal_color', 't', ?)",
        (snapshot_id,),
    )
    conn.commit()

    out = tmp_path / "feedback.jsonl"
    count = export_feedback(conn, str(out), "jsonl")
    assert count == 1

    row = json.loads(out.read_text().strip())
    assert row["label"] == "love"
    assert row["event"]["lineup"] == ["Mau P"]
    assert row["snapshot"]["score"] == 58.0
    assert row["snapshot"]["profile"] == {"mau p": 0.6}   # analysable without the DB


def test_impressions_export(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO impressions (event_id, user_id, surface, score_at_show, shown_at) "
        "VALUES ('ra:1', 'owner', 'calendar', 55.0, 't')"
    )
    conn.commit()
    out = tmp_path / "impressions.jsonl"
    assert export_impressions(conn, str(out), "jsonl") == 1
    assert json.loads(out.read_text().strip())["surface"] == "calendar"
