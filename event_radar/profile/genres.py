"""Genre-tag matching — bridge taste and the local scene by GENRE, not by name.

Exact-artist matching gave 0% coverage on RA-Milan (measured): your artists and
the lineup artists never share a name. But they share *genres* — your Cloonee/
Mau P are tech house / techno, and the Milan bill's Archie Hamilton (tech house)
and Charlotte de Witte (techno) sit right there. So we tag both sides with
Last.fm genres and score the overlap. Still a transparent formula, still no ML.

A stoplist drops non-genre noise (nationalities, "seen live") and the pollution
that ambiguous names pull in (a different "Fisher" tagged indie/rock).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Non-genre tags and ambiguous-name pollution to drop.
DEFAULT_STOPLIST = {
    "seen live", "favorites", "favourites", "favorite", "beautiful", "spotify",
    "female vocalists", "male vocalists", "vocalist", "indie", "pop", "rock",
    "british", "belgian", "belgium", "dutch", "netherlands", "uk", "united kingdom",
    "usa", "american", "italian", "italy", "german", "germany", "french", "france",
    "russian", "russia", "spanish", "spain", "australian", "canadian", "00s", "10s",
    "20s", "90s", "80s", "singer-songwriter", "chill", "cool", "amazing",
}


def _clean_tags(raw_tags, stoplist, top_n) -> dict[str, float]:
    tags: dict[str, float] = {}
    for tag, weight in raw_tags[: top_n * 2]:
        if tag in stoplist:
            continue
        tags[tag] = weight
        if len(tags) >= top_n:
            break
    return tags


def build_genre_profile(seed_weights, lastfm, expand_above=0.5, top_n=8, stoplist=None) -> dict[str, float]:
    """Aggregate seed artists' genre tags into a taste-genre profile {tag: weight}."""
    stoplist = stoplist or DEFAULT_STOPLIST
    profile: dict[str, float] = {}
    for artist, artist_weight in seed_weights.items():
        if artist_weight <= expand_above:
            continue
        for tag, tag_weight in _clean_tags(lastfm.top_tags(artist), stoplist, top_n).items():
            profile[tag] = profile.get(tag, 0.0) + artist_weight * tag_weight

    if not profile:
        return {}
    ceiling = max(profile.values())
    for tag in profile:
        profile[tag] = profile[tag] / ceiling  # normalise to [0,1]
    return profile


def enrich_artist_tags(connection, lastfm, artist_names, top_n=8, stoplist=None) -> int:
    """Fetch + cache genre tags for artists not already stored. Returns how many fetched."""
    stoplist = stoplist or DEFAULT_STOPLIST
    existing = set()
    for row in connection.execute("SELECT artist_name_normalized FROM artist_tags"):
        existing.add(row["artist_name_normalized"])

    now = datetime.now(timezone.utc).isoformat()
    fetched = 0
    for name in artist_names:
        if name in existing:
            continue
        tags = _clean_tags(lastfm.top_tags(name), stoplist, top_n)
        connection.execute(
            "INSERT INTO artist_tags (artist_name_normalized, tags_json, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(artist_name_normalized) DO UPDATE SET tags_json=excluded.tags_json, updated_at=excluded.updated_at",
            (name, json.dumps(tags, ensure_ascii=False), now),
        )
        fetched += 1
    connection.commit()
    return fetched


def load_artist_tags(connection) -> dict[str, dict[str, float]]:
    tags_map = {}
    for row in connection.execute("SELECT artist_name_normalized, tags_json FROM artist_tags"):
        tags_map[row["artist_name_normalized"]] = json.loads(row["tags_json"]) if row["tags_json"] else {}
    return tags_map


def artist_genre_affinity(artist_tags: dict[str, float], genre_profile: dict[str, float]) -> float:
    """How much one artist's genres overlap the taste profile, in [0,1]."""
    if not artist_tags or not genre_profile:
        return 0.0
    score = 0.0
    for tag, weight in artist_tags.items():
        score += weight * genre_profile.get(tag, 0.0)
    return min(1.0, score)


def save_genre_profile(connection, profile: dict[str, float]) -> None:
    connection.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES ('genre_profile', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (json.dumps(profile, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


def load_genre_profile(connection) -> dict[str, float]:
    row = connection.execute("SELECT value FROM app_state WHERE key = 'genre_profile'").fetchone()
    return json.loads(row["value"]) if row and row["value"] else {}
