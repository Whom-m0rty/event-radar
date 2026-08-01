"""Tests for the Spotify web-player parser on real-shaped tracklist HTML."""
from event_radar.profile.spotify_html import (
    build_seed_affinity,
    parse_liked_html,
    parse_liked_text,
)

# Trimmed but structurally real: two rows, multi-artist, an &amp; entity, Cyrillic.
_FIXTURE = """
<div data-testid="tracklist-row"><a data-testid="internal-track-link" href="/track/AAA1">\
<div class="x" data-encore-id="text" dir="auto">NO LOVE</div></a>\
<a href="/artist/1" tabindex="-1">Polovinka</a>, <a href="/artist/2" tabindex="-1">Joviee</a>\
<a href="/album/9" tabindex="-1">NO LOVE</a></div>
<div data-testid="tracklist-row"><a data-testid="internal-track-link" href="/track/BBB2">\
<div class="x" data-encore-id="text" dir="auto">Want It</div></a>\
<a href="/artist/3" tabindex="-1">Pola &amp; Bryson</a>, <a href="/artist/4" tabindex="-1">Марк</a></div>
<div data-testid="tracklist-row-placeholder"><div class="skeleton"></div></div>
"""


def test_parse_extracts_tracks_and_artists():
    tracks = parse_liked_html(_FIXTURE)
    assert len(tracks) == 2
    assert tracks[0]["track_id"] == "AAA1"
    assert tracks[0]["title"] == "NO LOVE"
    assert tracks[0]["artists"] == ["Polovinka", "Joviee"]


def test_parse_unescapes_entities_and_keeps_cyrillic():
    tracks = parse_liked_html(_FIXTURE)
    assert tracks[1]["artists"] == ["Pola & Bryson", "Марк"]


def test_parse_ignores_album_and_placeholder_rows():
    tracks = parse_liked_html(_FIXTURE)
    # No album name leaked in as an artist; placeholder row produced no track.
    all_artists = [name for track in tracks for name in track["artists"]]
    assert "NO LOVE" not in all_artists


def test_seed_affinity_frequency_weighting():
    tracks = [
        {"artists": ["Joviee"]},
        {"artists": ["Polovinka", "Joviee"]},   # Joviee liked twice -> higher
    ]
    affinity = build_seed_affinity(tracks, {"seed": {"base_weight": 0.6, "step_per_extra_like": 0.1}})
    assert affinity["joviee"].weight == 0.7
    assert affinity["polovinka"].weight == 0.6
    assert affinity["joviee"].source == "spotify_liked"


def test_parse_liked_text():
    tracks = parse_liked_text("Polovinka, Joviee\nCREAM SODA\n")
    assert len(tracks) == 2
    assert tracks[0]["artists"] == ["Polovinka", "Joviee"]
