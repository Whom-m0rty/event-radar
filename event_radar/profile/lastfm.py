"""Last.fm taste expansion via artist.getSimilar (spec 4.4).

For each strong seed artist we pull similar artists and add them with a decayed
weight = decay * source_weight * similarity. One level deep, not recursive.
Responses are cached on disk: getSimilar is deterministic, so leave-one-out
evaluation (which reuses the same lookups many times) stays cheap.
"""
from __future__ import annotations

import logging

import requests
import requests_cache

logger = logging.getLogger(__name__)

_ENDPOINT = "https://ws.audioscrobbler.com/2.0/"


class LastFm:
    def __init__(self, api_key: str, cache_path: str, limit: int = 20, cache_expire_hours: float = 720.0) -> None:
        self.api_key = api_key
        self.limit = limit
        self.session = requests_cache.CachedSession(
            cache_name=cache_path,
            backend="sqlite",
            expire_after=cache_expire_hours * 3600,
        )

    def top_tags(self, artist_name: str) -> list[tuple[str, float]]:
        """Return [(tag, weight 0..1)] for an artist (genre-ish tags), or []."""
        params = {
            "method": "artist.gettoptags",
            "artist": artist_name,
            "api_key": self.api_key,
            "format": "json",
            "autocorrect": 1,
        }
        try:
            response = self.session.get(_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            logger.warning("Last.fm getTopTags failed for %r: %s", artist_name, error)
            return []
        if "error" in payload:
            return []
        results: list[tuple[str, float]] = []
        for entry in payload.get("toptags", {}).get("tag", []):
            name = entry.get("name")
            count = entry.get("count")
            if name and count is not None:
                results.append((name.strip().lower(), float(count) / 100.0))
        return results

    def similar(self, artist_name: str) -> list[tuple[str, float]]:
        """Return [(similar_artist_name, match_score)] for an artist, or []."""
        params = {
            "method": "artist.getsimilar",
            "artist": artist_name,
            "api_key": self.api_key,
            "format": "json",
            "limit": self.limit,
            "autocorrect": 1,
        }
        try:
            response = self.session.get(_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            logger.warning("Last.fm getSimilar failed for %r: %s", artist_name, error)
            return []

        if "error" in payload:
            logger.warning("Last.fm error for %r: %s", artist_name, payload.get("message"))
            return []

        results: list[tuple[str, float]] = []
        for entry in payload.get("similarartists", {}).get("artist", []):
            name = entry.get("name")
            match = entry.get("match")
            if name and match is not None:
                results.append((name, float(match)))
        return results
