"""Feature extraction — the SINGLE place features are built.

Both scoring and the feature_snapshots (written when feedback arrives) call this
exact function. If scoring built features one way and snapshots another, a model
trained later on the snapshots would see inputs that differ from what production
scores — that mismatch is called train/serve skew, and it silently wrecks any
model. One function, one truth, no skew.

build_features(event, affinity) takes only the event row and the taste map, so it
is pure and testable. Config-dependent choices (which weekdays count as weekend,
which venues are whitelisted) live in score_event, not here.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from event_radar.profile.normalize import normalize_lineup

logger = logging.getLogger(__name__)


def _as_bool(value) -> bool:
    return bool(value)


def _weekday(starts_at: str | None) -> int | None:
    """Weekday 0=Mon..6=Sun from an ISO local timestamp (RA gives Europe/Rome)."""
    if not starts_at:
        return None
    try:
        return datetime.fromisoformat(starts_at).weekday()
    except ValueError:
        logger.warning("Could not parse starts_at=%r for weekday", starts_at)
        return None


def build_features(event: dict, affinity: dict[str, float]) -> dict:
    """Return the raw feature dict for an event given the taste map (name->weight)."""
    lineup_raw = event.get("lineup_raw")
    raw_names = json.loads(lineup_raw) if lineup_raw else []
    lineup = normalize_lineup(raw_names)

    matched: dict[str, float] = {}
    for name in lineup:
        if name in affinity:
            matched[name] = affinity[name]

    music_weights = list(matched.values())
    music_max = max(music_weights) if music_weights else 0.0
    music_mean = (sum(music_weights) / len(music_weights)) if music_weights else 0.0

    return {
        "lineup_size": len(lineup),
        "matched_artists": matched,        # normalized name -> weight
        "music_max": music_max,
        "music_mean": music_mean,
        "is_free": _as_bool(event.get("is_free")),
        "is_open_air": _as_bool(event.get("is_open_air")),
        "price_min": event.get("price_min"),
        "weekday": _weekday(event.get("starts_at")),
        "venue_name": event.get("venue_name"),
    }
