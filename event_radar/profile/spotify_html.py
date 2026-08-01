"""Build the taste profile from the Spotify web player instead of the Web API.

Why this exists: Spotify gates the Web API behind the app owner having Premium.
On a Free account we fall back to the user's own data, copied straight from the
web player DOM (Liked Songs or a playlist). It is their data, their copy — no API.

Two inputs:
  * parse_liked_html: the tracklist HTML (rows carry /track/ and /artist/ links)
  * parse_liked_text: one track per line, artists comma-separated (manual paste)

Both yield the same shape: [{track_id, title, artists:[raw names]}]. Tracks are
stored deduped by track_id, so re-pasting overlapping scroll chunks is safe.

Weighting (transparent, no ML): an artist starts at base_weight for being liked
once, +step per extra liked track, capped. Liking three tracks by someone counts
for more than one — a simple, defensible frequency signal.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from event_radar.profile.build import AffinityEntry
from event_radar.profile.normalize import normalize_artist

logger = logging.getLogger(__name__)

# A real row starts at data-testid="tracklist-row" — note the closing quote, so
# the "tracklist-row-placeholder" skeleton rows are NOT split points.
_ROW_MARKER = 'data-testid="tracklist-row"'
_TRACK_RE = re.compile(r'href="/track/([A-Za-z0-9]+)"')
_TITLE_RE = re.compile(r'data-testid="internal-track-link"[^>]*>\s*<div[^>]*>(.*?)</div>', re.S)
_ARTIST_RE = re.compile(r'href="/artist/[A-Za-z0-9]+"[^>]*>(.*?)</a>', re.S)


def parse_liked_html(html_text: str) -> list[dict]:
    """Extract tracks from Spotify web-player tracklist HTML."""
    tracks: list[dict] = []
    segments = html_text.split(_ROW_MARKER)
    # segments[0] is the header before the first row.
    for segment in segments[1:]:
        track_match = _TRACK_RE.search(segment)
        if track_match is None:
            continue
        track_id = track_match.group(1)

        title_match = _TITLE_RE.search(segment)
        title = html_lib.unescape(title_match.group(1)).strip() if title_match else None

        artists = []
        for raw in _ARTIST_RE.findall(segment):
            name = html_lib.unescape(raw).strip()
            if name:
                artists.append(name)

        if artists:
            tracks.append({"track_id": track_id, "title": title, "artists": artists})
    return tracks


def parse_liked_text(text: str) -> list[dict]:
    """One track per line, artists comma-separated. Synthetic id = hash of line."""
    tracks: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        track_id = "txt:" + hashlib.md5(stripped.encode("utf-8")).hexdigest()[:16]
        artists = [part.strip() for part in stripped.split(",") if part.strip()]
        if artists:
            tracks.append({"track_id": track_id, "title": None, "artists": artists})
    return tracks


def upsert_seed_tracks(connection: sqlite3.Connection, tracks: list[dict]) -> int:
    """Store tracks, deduped by track_id. Returns how many are now in the table."""
    now = datetime.now(timezone.utc).isoformat()
    for track in tracks:
        connection.execute(
            "INSERT INTO seed_tracks (track_id, title, artists_json, source, imported_at) "
            "VALUES (?,?,?,'spotify_liked',?) "
            "ON CONFLICT(track_id) DO UPDATE SET "
            "title=excluded.title, artists_json=excluded.artists_json, imported_at=excluded.imported_at",
            (track["track_id"], track.get("title"), json.dumps(track["artists"], ensure_ascii=False), now),
        )
    connection.commit()
    row = connection.execute("SELECT COUNT(*) AS n FROM seed_tracks").fetchone()
    return row["n"]


def build_seed_affinity(tracks: list[dict], config: dict | None = None) -> dict[str, AffinityEntry]:
    """Frequency-weighted affinity over the liked tracks."""
    seed_cfg = (config or {}).get("seed", {})
    base = seed_cfg.get("base_weight", 0.6)
    step = seed_cfg.get("step_per_extra_like", 0.1)
    cap = seed_cfg.get("max_weight", 1.0)

    counts: dict[str, int] = {}
    for track in tracks:
        seen_in_track: set[str] = set()
        for raw in track["artists"]:
            for name in normalize_artist(raw):
                if name in seen_in_track:
                    continue
                seen_in_track.add(name)
                counts[name] = counts.get(name, 0) + 1

    affinity: dict[str, AffinityEntry] = {}
    for name, count in counts.items():
        weight = min(cap, base + step * (count - 1))
        affinity[name] = AffinityEntry(weight=weight, source="spotify_liked")
    logger.info("Seed affinity: %d artists from %d tracks", len(affinity), len(tracks))
    return affinity


def build_seed_affinity_from_db(connection: sqlite3.Connection, config: dict | None = None) -> dict[str, AffinityEntry]:
    """Rebuild seed affinity from every stored seed track."""
    tracks = []
    for row in connection.execute("SELECT artists_json FROM seed_tracks"):
        tracks.append({"artists": json.loads(row["artists_json"])})
    return build_seed_affinity(tracks, config)
