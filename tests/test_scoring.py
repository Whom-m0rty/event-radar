"""Tests for build_features and score_event, incl. music-as-booster behaviour."""
import json

from event_radar.scoring.features import build_features
from event_radar.scoring.score import score_event

CONFIG = {
    "weights": {
        "base": 18, "music": 40, "bonus_free": 15, "bonus_openair": 12,
        "bonus_cheap": 8, "penalty_price": 0.4, "bonus_weekend": 10, "bonus_venue": 15,
    },
    "music": {"max_weight": 0.7, "mean_weight": 0.3},
    "price": {"cheap_threshold_eur": 15, "penalty_above_eur": 25},
    "weekend_days": [4, 5],
    "venue_whitelist": ["Tempio del Futuro Perduto"],
}


def _event(**overrides):
    base = {
        "lineup_raw": json.dumps(["Mau P", "Some Unknown"]),
        "is_free": 0, "is_open_air": 0, "price_min": None,
        "starts_at": "2026-08-01T23:00:00.000",  # a Saturday
        "venue_name": "Some Club",
    }
    base.update(overrides)
    return base


def test_features_match_music():
    affinity = {"mau p": 0.6}
    features = build_features(_event(), affinity)
    assert features["matched_artists"] == {"mau p": 0.6}
    assert features["music_max"] == 0.6
    assert features["weekday"] == 5  # Saturday


def test_no_music_still_scores_from_base_and_weekend():
    # Booster design: an event with zero taste overlap still gets a real score.
    features = build_features(_event(), affinity={})
    score, breakdown = score_event(features, CONFIG)
    assert "music" not in breakdown
    assert breakdown["base"] == 18
    assert breakdown["weekend"] == 10
    assert score == 28


def test_music_adds_on_top():
    features = build_features(_event(), affinity={"mau p": 1.0})
    score, breakdown = score_event(features, CONFIG)
    # music_component = 0.7*1.0 + 0.3*1.0 = 1.0 -> 40
    assert breakdown["music"] == 40.0
    assert score == 28 + 40


def test_free_excludes_price_branches():
    features = build_features(_event(is_free=1, price_min=0), affinity={})
    score, breakdown = score_event(features, CONFIG)
    assert breakdown["free"] == 15
    assert "cheap" not in breakdown and "price_penalty" not in breakdown


def test_price_penalty_above_threshold():
    features = build_features(_event(price_min=45), affinity={})
    _score, breakdown = score_event(features, CONFIG)
    # (45-25)*0.4 = 8
    assert breakdown["price_penalty"] == -8.0


def test_venue_whitelist_bonus():
    features = build_features(_event(venue_name="Tempio del Futuro Perduto"), affinity={})
    _score, breakdown = score_event(features, CONFIG)
    assert breakdown["venue"] == 15


def test_score_clamped_to_100():
    features = build_features(
        _event(is_free=1, is_open_air=1, venue_name="Tempio del Futuro Perduto"),
        affinity={"mau p": 1.0},
    )
    score, _breakdown = score_event(features, CONFIG)
    assert score == 100.0
