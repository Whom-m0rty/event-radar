"""The cron pipeline: fetch -> score -> push-calendar -> sync-feedback.

One entry point so a systemd timer (or launchd) runs a single command. Google
errors propagate so the CLI can ping Telegram and prompt re-auth.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from event_radar.calendar.format import build_description, build_title
from event_radar.feedback.sync import sync_calendar_colors
from event_radar.scoring.features import build_features
from event_radar.scoring.score import score_event
from event_radar.sources.ra import ResidentAdvisor
from event_radar.storage import upsert_events

logger = logging.getLogger(__name__)


def _affinity(connection) -> dict[str, float]:
    affinity = {}
    for row in connection.execute("SELECT artist_name_normalized, weight FROM affinity"):
        affinity[row["artist_name_normalized"]] = row["weight"]
    return affinity


def fetch_step(config, connection, contact: str, window_days: int | None = None) -> tuple[int, int]:
    source_cfg = config.get("source", {})
    ra = ResidentAdvisor(
        contact=contact,
        area_id=source_cfg.get("area_id"),
        user_agent_template=source_cfg.get("user_agent", "EventRadar/0.1 (+mailto:{contact})"),
        request_delay_seconds=source_cfg.get("request_delay_seconds", 2.0),
        cache_expire_hours=source_cfg.get("cache_expire_hours", 6.0),
        max_retries=source_cfg.get("max_retries", 4),
    )
    if ra.area_id is None:
        ra.area_id = ra.resolve_area(source_cfg.get("area_search_term", "milan"))
    days = window_days if window_days is not None else source_cfg.get("window_days", 60)
    start = date.today()
    end = start + timedelta(days=days)
    events = ra.fetch(source_cfg.get("city", "milan"), start.isoformat(), end.isoformat())
    return upsert_events(connection, events)


def score_step(config, connection) -> int:
    scoring_cfg = config.get("scoring", {})
    affinity = _affinity(connection)
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("DELETE FROM scores")
    for event in connection.execute("SELECT * FROM events").fetchall():
        features = build_features(dict(event), affinity)
        value, breakdown = score_event(features, scoring_cfg)
        connection.execute(
            "INSERT INTO scores (event_id, score, breakdown, computed_at) VALUES (?,?,?,?)",
            (event["id"], value, json.dumps(breakdown), now),
        )
    connection.commit()
    return connection.execute("SELECT COUNT(*) FROM scores").fetchone()[0]


def calendar_step(config, connection, gcal, bot_username: str | None) -> tuple[int, int]:
    """Push above-threshold events and sync colour feedback. May raise on auth failure."""
    scoring_cfg = config.get("scoring", {})
    threshold = scoring_cfg.get("push_threshold", 35)
    affinity = _affinity(connection)

    affinity_meta = {}
    for row in connection.execute("SELECT artist_name_normalized, source, origin FROM affinity"):
        affinity_meta[row["artist_name_normalized"]] = {"source": row["source"], "origin": row["origin"]}

    calendar_id = gcal.ensure_calendar(config.get("calendar", {}).get("name", "Event Radar"))

    pushed = 0
    for event in connection.execute("SELECT * FROM events").fetchall():
        event_dict = dict(event)
        features = build_features(event_dict, affinity)
        value, breakdown = score_event(features, scoring_cfg)
        if value < threshold:
            continue
        title = build_title(event_dict, value)
        description = build_description(event_dict, features, breakdown, config, affinity_meta, bot_username)
        gcal.push_event(calendar_id, event_dict, title, description, value)
        pushed += 1

    written = sync_calendar_colors(connection, gcal.list_events(calendar_id), affinity, config)
    return pushed, written
