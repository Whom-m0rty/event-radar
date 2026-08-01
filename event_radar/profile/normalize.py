"""Artist-name normalization — the single most failure-prone function here.

If a name is not parsed the same way on both sides (taste profile and event
lineup), its weight silently becomes zero and the event's music score collapses.
So this is deliberately conservative and heavily tested (spec section 4.5).

`normalize_artist` takes ONE raw lineup fragment and returns a LIST, because a
fragment can encode several artists ("A b2b B") and must be split. A plain single
artist name returns a one-element list.
"""
from __future__ import annotations

import re
import unicodedata

# Split only on unambiguous multi-artist separators. We do NOT split on "&" or
# "/" — that would wreck one-act names like "Above & Beyond".
_SPLIT = re.compile(r"\s+(?:b2b|b3b|b4b|f2f|vs\.?|v/s)\s+|,", re.IGNORECASE)

# Parenthetical qualifiers: "(JP)", "(UK)", "(live)", disambiguation "(1)".
_PAREN = re.compile(r"\([^)]*\)")

# "Support:", "Special Guests:" — drop the label, keep whatever real name follows.
_LABEL_PREFIX = re.compile(r"^\s*(?:support|special guests?|guests?)\s*:\s*", re.IGNORECASE)

# Performance-type suffixes to strip from the end of a token.
_SUFFIXES = (
    "all night long",
    "hybrid set",
    "live set",
    "dj set",
    "live",
    "b2b",
)

# Generic filler that is not a real artist.
_JUNK = {
    "support", "guest", "guests", "special guest", "special guests",
    "local heroes", "and friends", "friends", "more", "tba", "residents",
    "resident", "vs",
}


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _clean_token(token: str) -> str:
    token = _LABEL_PREFIX.sub("", token)
    token = _PAREN.sub(" ", token)
    token = strip_diacritics(token).lower()

    # Strip performance suffixes repeatedly ("Artist live b2b" edge cases).
    trimming = True
    while trimming:
        trimming = False
        stripped = token.strip()
        for suffix in _SUFFIXES:
            if stripped.endswith(suffix):
                token = stripped[: -len(suffix)]
                trimming = True
                break

    # Collapse separators to single spaces, keep letters/digits/dot/apostrophe
    # (dots matter for "M.I.T.A."). Then squeeze whitespace.
    token = re.sub(r"[\s\-_/]+", " ", token)
    token = re.sub(r"[^\w\s.'&]", "", token)
    token = re.sub(r"\s+", " ", token).strip()
    return token


def normalize_artist(raw: str | None) -> list[str]:
    """Return the clean, lowercased artist names encoded in one raw fragment."""
    if not raw:
        return []

    names: list[str] = []
    seen: set[str] = set()
    for part in _SPLIT.split(raw):
        cleaned = _clean_token(part)
        if not cleaned or cleaned in _JUNK:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            names.append(cleaned)
    return names


def normalize_lineup(raw_names: list[str]) -> list[str]:
    """Normalize a list of raw names (RA gives structured artists) into a flat,
    de-duplicated list of clean names."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        for name in normalize_artist(raw):
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result
