"""Tests for the RA source that need no network: pure parsing helpers.

Live requests never happen in tests (spec section 13) — network-shaped tests run
against saved fixtures, added as we capture them.
"""
from event_radar.sources.ra import ResidentAdvisor, guess_open_air, parse_cost


def test_parse_cost_free_words():
    assert parse_cost("Free", None) == (0.0, True)
    assert parse_cost("Gratis before 11pm", None) == (0.0, True)


def test_parse_cost_single_and_tiered():
    assert parse_cost("50", None) == (50.0, False)
    assert parse_cost("0-5-10€", None) == (0.0, False)   # tiered: floor 0, not all-free
    assert parse_cost("0", None) == (0.0, True)           # exactly zero -> free


def test_parse_cost_empty_falls_back_to_ticketed():
    assert parse_cost("", False) == (None, True)
    assert parse_cost("", True) == (None, None)
    assert parse_cost(None, None) == (None, None)


def test_parse_cost_uses_ticket_prices():
    assert parse_cost("", True, ticket_prices=[25, 15, None]) == (15.0, False)


def test_guess_open_air_matches_markers():
    assert guess_open_air("Vision Open Air", "Some Venue") is True
    assert guess_open_air("Le Cannibale", "La Terrazza") is True
    assert guess_open_air("Techno Night", "Basement Club") is False
    assert guess_open_air(None, None) is False


def _make_source() -> ResidentAdvisor:
    # area_id/contact are irrelevant for pure parsing; no session call is made.
    return ResidentAdvisor(contact="test@example.com", area_id=347)


def test_parse_row_maps_core_fields():
    source = _make_source()
    row = {
        "id": "1",
        "listingDate": "2026-08-01T00:00:00.000",
        "event": {
            "id": "2430414",
            "date": "2026-08-01T00:00:00.000",
            "startTime": "2026-08-01T23:00:00.000",
            "endTime": "2026-08-02T06:00:00.000",
            "title": "NOTTE TEKNO",
            "contentUrl": "/events/2430414",
            "isTicketed": True,
            "attending": 270,
            "cost": "0-5-10€",
            "tickets": [],
            "venue": {"id": "9", "name": "Tempio del Futuro Perduto"},
            "artists": [{"id": "1", "name": "Tania Kim"}, {"id": "2", "name": "Waldo"}],
        },
    }
    parsed = source._parse_row(row, "milan")
    assert parsed is not None
    assert parsed.id == "ra:2430414"
    assert parsed.title == "NOTTE TEKNO"
    assert parsed.venue_name == "Tempio del Futuro Perduto"
    assert parsed.lineup == ["Tania Kim", "Waldo"]
    assert parsed.url == "https://ra.co/events/2430414"
    assert parsed.price_min == 0.0       # tiered 0-5-10 -> floor 0
    assert parsed.is_free is False       # but not all-free


def test_parse_row_skips_broken_row():
    source = _make_source()
    assert source._parse_row({"event": None}, "milan") is None
    assert source._parse_row({}, "milan") is None
