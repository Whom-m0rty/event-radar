"""Tests for calendar title/description formatting (spec section 13)."""
import json

from event_radar.calendar.format import build_description, build_title

CONFIG = {"calendar": {"color_feedback": {
    "10": {"dimension": "intent", "label": "love"},
    "5": {"dimension": "intent", "label": "meh"},
}}}


def _event(**overrides):
    base = {
        "id": "ra:123",
        "title": "Vision open air w Charlotte de Witte",
        "venue_name": "Ex Macello",
        "starts_at": "2026-08-01T23:00:00.000",
        "ends_at": "2026-08-02T06:00:00.000",
        "lineup_raw": json.dumps(["Charlotte de Witte", "Mau P"]),
        "is_free": 1, "is_open_air": 1, "price_min": None, "url": "https://ra.co/events/123",
    }
    base.update(overrides)
    return base


def test_title_free():
    assert build_title(_event(), 55).startswith("[FREE] [55] ")


def test_title_unknown_price():
    assert build_title(_event(is_free=0), 72) == "[€?] [72] Vision open air w Charlotte de Witte"


def test_title_with_price():
    assert build_title(_event(is_free=0, price_min=25), 72).startswith("[€25] [72] ")


def test_title_is_stable_for_idempotency():
    # Same event + same score must produce identical title (calendar dedupe relies on it).
    event = _event()
    assert build_title(event, 55) == build_title(dict(event), 55)


def test_description_marks_matched_artist_and_lists_links():
    features = {"is_open_air": True, "matched_artists": {"mau p": 0.6}}
    breakdown = {"base": 18.0, "free": 15.0, "open_air": 12.0, "weekend": 10.0}
    text = build_description(_event(), features, breakdown, CONFIG)
    assert "★ your taste" in text          # Mau P is in taste
    assert "open.spotify.com/search" in text
    assert "Recolour to rate: love · meh" in text
    assert "Why this score:" in text


def test_description_similar_mark_with_origin():
    features = {"is_open_air": False, "matched_artists": {"mau p": 0.24}}
    meta = {"mau p": {"source": "lastfm_similar", "origin": "fisher"}}
    text = build_description(_event(is_open_air=0), features, {"base": 18.0}, CONFIG, affinity_meta=meta)
    assert "≈ similar to fisher" in text
