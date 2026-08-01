"""Tests for normalize_artist on real-looking RA lineup strings (spec 4.5)."""
from event_radar.profile.normalize import normalize_artist, normalize_lineup


def test_plain_name():
    assert normalize_artist("Marcel Dettmann") == ["marcel dettmann"]


def test_lowercase_and_whitespace():
    assert normalize_artist("  Ben   Klock ") == ["ben klock"]


def test_strip_diacritics():
    assert normalize_artist("SHŪ") == ["shu"]
    assert normalize_artist("Béatrice") == ["beatrice"]


def test_drop_disambiguation_number():
    assert normalize_artist("Sinai (1)") == ["sinai"]


def test_drop_country_tag():
    assert normalize_artist("DJ Nobu (JP)") == ["dj nobu"]
    assert normalize_artist("Blawan (UK)") == ["blawan"]


def test_strip_live_suffix():
    assert normalize_artist("DRUM THE SYSTEM live") == ["drum the system"]


def test_strip_dj_set_and_all_night_long():
    assert normalize_artist("Ricardo Villalobos all night long") == ["ricardo villalobos"]
    assert normalize_artist("Objekt DJ set") == ["objekt"]


def test_split_b2b():
    assert normalize_artist("Marcel Dettmann b2b Ben Klock") == ["marcel dettmann", "ben klock"]


def test_split_b3b_and_vs():
    assert normalize_artist("A b3b B") == ["a", "b"]
    assert normalize_artist("Surgeon vs Lady Starlight") == ["surgeon", "lady starlight"]


def test_keep_dots_in_acronym():
    assert normalize_artist("M.I.T.A.") == ["m.i.t.a."]


def test_drop_support_label_and_filler():
    assert normalize_artist("Support: Local Heroes") == []
    assert normalize_artist("Special Guests: TBA") == []


def test_do_not_split_ampersand_name():
    # "Above & Beyond" is one act — must not split.
    assert normalize_artist("Above & Beyond") == ["above & beyond"]


def test_full_messy_lineup_string():
    raw = "Marcel Dettmann b2b Ben Klock, DJ Nobu (JP), Support: Local Heroes"
    assert normalize_artist(raw) == ["marcel dettmann", "ben klock", "dj nobu"]


def test_normalize_lineup_dedupes():
    assert normalize_lineup(["Objekt", "objekt DJ set", "SHŪ"]) == ["objekt", "shu"]
