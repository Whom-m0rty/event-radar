"""Build the taste profile: a flat {normalized_artist -> weight in [0,1]} dict.

This step covers the Spotify half (top + followed). Last.fm expansion is layered
on in the next step. Weights follow spec section 4.3:
  short_term -> 1.0, medium_term -> 0.8, long_term -> 0.6
  within a list: multiply by inverse rank  1 - 0.5 * (position / length)
  same artist in several lists: take the MAX (not the sum)
  followed but never in a top list: 0.5
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from event_radar.profile.normalize import normalize_artist
from event_radar.profile.spotify import SpotifyClient

logger = logging.getLogger(__name__)


@dataclass
class AffinityEntry:
    weight: float
    source: str            # spotify_top | spotify_followed | lastfm_similar
    origin: str | None = None


_TIME_RANGE_WEIGHTS = {
    "short_term": 1.0,
    "medium_term": 0.8,
    "long_term": 0.6,
}


def _first_normalized(name: str) -> str | None:
    parts = normalize_artist(name)
    return parts[0] if parts else None


def build_spotify_affinity(client: SpotifyClient, config: dict | None = None) -> dict[str, AffinityEntry]:
    weights = _TIME_RANGE_WEIGHTS
    followed_weight = 0.5
    if config:
        spotify_cfg = config.get("spotify", {})
        weights = {
            "short_term": spotify_cfg.get("short_term_weight", 1.0),
            "medium_term": spotify_cfg.get("medium_term_weight", 0.8),
            "long_term": spotify_cfg.get("long_term_weight", 0.6),
        }
        followed_weight = spotify_cfg.get("followed_only_weight", 0.5)

    affinity: dict[str, AffinityEntry] = {}

    for time_range, base_weight in weights.items():
        items = client.top_artists(time_range, limit=50)
        count = len(items)
        for position, artist in enumerate(items):
            name = _first_normalized(artist.get("name", ""))
            if name is None:
                continue
            inverse_rank = 1.0 - 0.5 * (position / count) if count else 1.0
            weight = base_weight * inverse_rank
            existing = affinity.get(name)
            # Same artist across lists: keep the maximum weight.
            if existing is None or weight > existing.weight:
                affinity[name] = AffinityEntry(weight=weight, source="spotify_top")

    for artist in client.followed_artists():
        name = _first_normalized(artist.get("name", ""))
        if name is None:
            continue
        if name not in affinity:
            affinity[name] = AffinityEntry(weight=followed_weight, source="spotify_followed")

    logger.info("Spotify affinity: %d artists", len(affinity))
    return affinity


def expand_with_lastfm(
    base_affinity: dict[str, AffinityEntry],
    lastfm,
    config: dict | None = None,
) -> dict[str, AffinityEntry]:
    """Add Last.fm-similar artists to a base affinity (spec 4.4).

    One level deep: only base artists above `expand_above` are expanded. An added
    artist gets weight = decay * source_weight * similarity, merged by MAX. Base
    artists always keep their own (higher) weight — expansion never demotes them.
    Returns a NEW dict; the base is left untouched.
    """
    lastfm_cfg = (config or {}).get("lastfm", {})
    expand_above = lastfm_cfg.get("expand_above", 0.5)
    decay = lastfm_cfg.get("decay", 0.4)

    merged: dict[str, AffinityEntry] = dict(base_affinity)

    for source_name, source_entry in base_affinity.items():
        if source_entry.weight <= expand_above:
            continue
        for similar_raw, match in lastfm.similar(source_name):
            similar_names = normalize_artist(similar_raw)
            if not similar_names:
                continue
            similar_name = similar_names[0]
            if similar_name in base_affinity:
                # Don't let a similarity edge overwrite a real liked artist.
                continue
            added_weight = decay * source_entry.weight * match
            existing = merged.get(similar_name)
            if existing is None or existing.source != "lastfm_similar" or added_weight > existing.weight:
                if existing is None or existing.source == "lastfm_similar":
                    merged[similar_name] = AffinityEntry(
                        weight=added_weight, source="lastfm_similar", origin=source_name
                    )
    return merged


def persist_affinity(
    connection: sqlite3.Connection,
    affinity: dict[str, AffinityEntry],
    replace_sources: tuple[str, ...],
) -> None:
    """Replace the rows for the given sources with the new affinity.

    Only the named sources are cleared, so e.g. rebuilding Spotify does not wipe
    Last.fm rows (and vice versa) unless asked.
    """
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in replace_sources)
    connection.execute(
        f"DELETE FROM affinity WHERE source IN ({placeholders})", replace_sources
    )
    for name, entry in affinity.items():
        connection.execute(
            "INSERT INTO affinity (artist_name_normalized, weight, source, origin, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(artist_name_normalized) DO UPDATE SET "
            "weight=excluded.weight, source=excluded.source, origin=excluded.origin, "
            "updated_at=excluded.updated_at",
            (name, entry.weight, entry.source, entry.origin, now),
        )
    connection.commit()
