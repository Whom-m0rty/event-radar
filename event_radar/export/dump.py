"""Export feedback as a self-contained dataset (spec section 11).

One row = one feedback fact, with the feature snapshot, score, breakdown and taste
profile from that moment glued inside. The file must be analysable on its own,
with no access to the live DB — so everything needed is denormalised into the row.
Impressions go to a separate file.

jsonl is native (no deps). parquet needs pandas+pyarrow (lazy import).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _feedback_rows(connection: sqlite3.Connection) -> list[dict]:
    query = (
        "SELECT f.id AS feedback_id, f.event_id, f.user_id, f.dimension, f.label, "
        "f.channel, f.created_at, f.note, "
        "e.title, e.venue_name, e.starts_at, e.url, e.is_free, e.is_open_air, "
        "e.price_min, e.lineup_raw, "
        "s.features_json, s.score, s.breakdown_json, s.profile_hash, s.profile_json "
        "FROM feedback_events f "
        "LEFT JOIN events e ON e.id = f.event_id "
        "LEFT JOIN feature_snapshots s ON s.id = f.snapshot_id "
        "ORDER BY f.created_at"
    )
    rows = []
    for row in connection.execute(query):
        rows.append({
            "feedback_id": row["feedback_id"],
            "event_id": row["event_id"],
            "user_id": row["user_id"],
            "dimension": row["dimension"],
            "label": row["label"],
            "channel": row["channel"],
            "created_at": row["created_at"],
            "note": row["note"],
            "event": {
                "title": row["title"],
                "venue_name": row["venue_name"],
                "starts_at": row["starts_at"],
                "url": row["url"],
                "is_free": row["is_free"],
                "is_open_air": row["is_open_air"],
                "price_min": row["price_min"],
                "lineup": json.loads(row["lineup_raw"]) if row["lineup_raw"] else [],
            },
            "snapshot": {
                "features": json.loads(row["features_json"]) if row["features_json"] else None,
                "score": row["score"],
                "breakdown": json.loads(row["breakdown_json"]) if row["breakdown_json"] else None,
                "profile_hash": row["profile_hash"],
                "profile": json.loads(row["profile_json"]) if row["profile_json"] else None,
            },
        })
    return rows


def _impression_rows(connection: sqlite3.Connection) -> list[dict]:
    rows = []
    for row in connection.execute("SELECT * FROM impressions ORDER BY shown_at"):
        rows.append(dict(row))
    return rows


def _write_jsonl(rows: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_parquet(rows: list[dict], path: Path) -> None:
    try:
        import pandas
    except ImportError as error:
        raise RuntimeError("parquet export needs pandas+pyarrow: pip install pandas pyarrow") from error
    # Nested dicts are kept as object columns; readable back with pandas.
    frame = pandas.json_normalize(rows)
    frame.to_parquet(path, index=False)


def export_feedback(connection: sqlite3.Connection, out_path: str, fmt: str) -> int:
    rows = _feedback_rows(connection)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        _write_jsonl(rows, path)
    elif fmt == "parquet":
        _write_parquet(rows, path)
    else:
        raise ValueError(f"unknown format {fmt!r} (use jsonl or parquet)")
    return len(rows)


def export_impressions(connection: sqlite3.Connection, out_path: str, fmt: str) -> int:
    rows = _impression_rows(connection)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        _write_jsonl(rows, path)
    elif fmt == "parquet":
        _write_parquet(rows, path)
    else:
        raise ValueError(f"unknown format {fmt!r} (use jsonl or parquet)")
    return len(rows)
