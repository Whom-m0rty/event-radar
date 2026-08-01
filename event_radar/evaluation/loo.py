"""Quality checks for the taste profile.

Two metrics, measuring two different things — read the docstrings, the difference
is the whole point (spec section 6):

  leave_one_out  — does the Last.fm graph let the REST of my library reconstruct a
                   held-out favorite? This measures the expansion's coherence, NOT
                   whether I'll enjoy a party. If recall is ~0.1 the similarity
                   graph is too thin for my taste and the music score is fiction.

  coverage_at_events — of the artists actually on Milan lineups, what fraction get
                   any affinity > 0? This is the honest one for *this* use case:
                   a rich Last.fm graph is worthless if it never touches the acts
                   that actually play here. LOO can look fine while coverage is
                   near zero (my taste and the local scene simply don't overlap).
"""
from __future__ import annotations

import json
import logging
import sqlite3

from event_radar.profile.normalize import normalize_artist, normalize_lineup

logger = logging.getLogger(__name__)


def _similar_lookup(lastfm, artist_names: list[str]) -> dict[str, list[tuple[str, float]]]:
    """Pre-fetch (and cache) getSimilar for every artist once."""
    lookup: dict[str, list[tuple[str, float]]] = {}
    for name in artist_names:
        lookup[name] = lastfm.similar(name)
    return lookup


def leave_one_out(
    seed_weights: dict[str, float],
    lastfm,
    expand_above: float = 0.5,
    decay: float = 0.4,
    k: int = 50,
) -> dict:
    """Hold out each seed artist, rebuild expansion from the rest, rank candidates.

    Returns recall@k and MRR. A held-out artist "hits" if the Last.fm expansion of
    the remaining seed surfaces it within the top-k candidates.
    """
    names = list(seed_weights.keys())
    lookup = _similar_lookup(lastfm, names)

    hits = 0
    reciprocal_rank_sum = 0.0
    evaluated = 0

    for held_out in names:
        candidates: dict[str, float] = {}
        for expander in names:
            if expander == held_out:
                continue
            expander_weight = seed_weights[expander]
            if expander_weight <= expand_above:
                continue
            for similar_raw, match in lookup[expander]:
                normalized = normalize_artist(similar_raw)
                if not normalized:
                    continue
                similar_name = normalized[0]
                weight = decay * expander_weight * match
                if weight > candidates.get(similar_name, 0.0):
                    candidates[similar_name] = weight

        evaluated += 1
        ranked = _ranked_names(candidates)
        position = _position_of(ranked, held_out)
        if position is not None and position <= k:
            hits += 1
            reciprocal_rank_sum += 1.0 / position

    recall = hits / evaluated if evaluated else 0.0
    mrr = reciprocal_rank_sum / evaluated if evaluated else 0.0
    return {"recall_at_k": recall, "mrr": mrr, "k": k, "held_out": evaluated, "hits": hits}


def _ranked_names(candidates: dict[str, float]) -> list[str]:
    def weight_of(item):
        return item[1]

    ordered = sorted(candidates.items(), key=weight_of, reverse=True)
    return [name for name, _weight in ordered]


def _position_of(ranked: list[str], target: str) -> int | None:
    for index, name in enumerate(ranked):
        if name == target:
            return index + 1  # 1-based rank
    return None


def coverage_at_events(connection: sqlite3.Connection, affinity_names: set[str]) -> dict:
    """How much of the actual Milan lineups the profile touches."""
    total_artists = 0
    covered_artists = 0
    total_events = 0
    events_with_any = 0

    for row in connection.execute("SELECT lineup_raw FROM events WHERE lineup_raw IS NOT NULL"):
        raw_names = json.loads(row["lineup_raw"])
        lineup = normalize_lineup(raw_names)
        if not lineup:
            continue
        total_events += 1
        any_hit = False
        for name in lineup:
            total_artists += 1
            if name in affinity_names:
                covered_artists += 1
                any_hit = True
        if any_hit:
            events_with_any += 1

    artist_coverage = covered_artists / total_artists if total_artists else 0.0
    event_coverage = events_with_any / total_events if total_events else 0.0
    return {
        "artist_coverage": artist_coverage,
        "event_coverage": event_coverage,
        "total_events": total_events,
        "events_with_any_match": events_with_any,
        "total_lineup_artists": total_artists,
        "matched_lineup_artists": covered_artists,
    }
