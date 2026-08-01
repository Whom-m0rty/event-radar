"""Tests for genre-tag matching (the fix for 0% name coverage)."""
import json

from event_radar.profile.genres import DEFAULT_STOPLIST, _clean_tags, artist_genre_affinity
from event_radar.scoring.features import build_features
from event_radar.scoring.score import score_event

SCORING = {
    "weights": {"base": 18, "music": 40, "genre": 30, "bonus_free": 15, "bonus_openair": 12,
                "bonus_cheap": 8, "penalty_price": 0.4, "bonus_weekend": 10, "bonus_venue": 15},
    "music": {"max_weight": 0.7, "mean_weight": 0.3},
    "genre": {"max_weight": 0.7, "mean_weight": 0.3},
    "price": {"cheap_threshold_eur": 15, "penalty_above_eur": 25},
    "weekend_days": [4, 5], "venue_whitelist": [],
}


def test_clean_tags_drops_stoplist_noise():
    raw = [("tech house", 1.0), ("indie", 0.9), ("belgian", 0.8), ("techno", 0.7)]
    cleaned = _clean_tags(raw, DEFAULT_STOPLIST, top_n=8)
    assert "tech house" in cleaned and "techno" in cleaned
    assert "indie" not in cleaned and "belgian" not in cleaned


def test_artist_genre_affinity_overlap():
    profile = {"house": 0.7, "tech house": 0.6, "techno": 0.3}
    # An artist tagged tech house / house overlaps a house-leaning taste.
    assert artist_genre_affinity({"tech house": 1.0, "house": 0.8}, profile) > 0.5
    # A jazz artist doesn't.
    assert artist_genre_affinity({"jazz": 1.0}, profile) == 0.0


def test_build_features_genre_from_context():
    event = {"lineup_raw": json.dumps(["Archie Hamilton"]), "starts_at": "2026-08-01T23:00:00.000"}
    genre_ctx = {"artist_tags": {"archie hamilton": {"tech house": 1.0}}, "profile": {"tech house": 1.0}}
    features = build_features(event, affinity={}, genre_ctx=genre_ctx)
    assert features["genre_max"] == 1.0
    assert features["music_max"] == 0.0   # no exact-name match


def test_score_includes_genre_booster_when_names_miss():
    event = {"lineup_raw": json.dumps(["Archie Hamilton"]), "is_free": 0, "is_open_air": 0,
             "price_min": None, "starts_at": "2026-08-01T23:00:00.000", "venue_name": "X"}
    genre_ctx = {"artist_tags": {"archie hamilton": {"tech house": 1.0}}, "profile": {"tech house": 1.0}}
    features = build_features(event, affinity={}, genre_ctx=genre_ctx)
    score, breakdown = score_event(features, SCORING)
    assert "genre" in breakdown and breakdown["genre"] == 30.0
    assert "music" not in breakdown
