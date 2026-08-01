"""Feature snapshots — the reason 300 labels become 300 *trainable* labels.

The taste profile changes every week. A label ("I loved this event") is only
useful later if we also froze WHAT the system knew at that moment: the event's
features, its score/breakdown, and the whole taste profile. Without the snapshot,
in six months there is nothing to attach the label to (spec section 7, rule 2).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from event_radar.scoring.features import build_features
from event_radar.scoring.score import score_event


def create_snapshot(
    connection: sqlite3.Connection,
    event: dict,
    affinity: dict[str, float],
    scoring_cfg: dict,
) -> int:
    """Freeze features + score + the full profile for one event. Returns snapshot id."""
    features = build_features(event, affinity)
    score, breakdown = score_event(features, scoring_cfg)

    # The whole profile, verbatim (spec: yes, duplicated, yes, on purpose).
    profile_json = json.dumps(affinity, sort_keys=True, ensure_ascii=False)
    profile_hash = hashlib.sha256(profile_json.encode("utf-8")).hexdigest()

    cursor = connection.execute(
        "INSERT INTO feature_snapshots "
        "(event_id, features_json, score, breakdown_json, profile_hash, profile_json, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            event["id"],
            json.dumps(features, ensure_ascii=False),
            score,
            json.dumps(breakdown, ensure_ascii=False),
            profile_hash,
            profile_json,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid
