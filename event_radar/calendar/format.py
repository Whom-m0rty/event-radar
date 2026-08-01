"""Format a calendar event's title and description (spec section 9).

Pure string building, no network — fully unit-tested. The calendar module feeds
it event rows, scored features, breakdown, and optional per-artist affinity meta
(source/origin) so known acts can be marked ★ (your taste) or ≈ (similar to X).
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime


def _price_tag(event: dict) -> str:
    if event.get("is_free"):
        return "[FREE]"
    price = event.get("price_min")
    if price is None:
        return "[€?]"          # RA listing has no price; filled by a later detail pass
    return f"[€{int(price)}]"


def build_title(event: dict, score: float) -> str:
    """e.g. '[FREE] [87] Le Cannibale w/ DJ Tennis' or '[€?] [55] Vision Open Air'."""
    title = event.get("title") or "(untitled)"
    return f"{_price_tag(event)} [{int(round(score))}] {title}"


def _listen_links(artist: str) -> str:
    quoted = urllib.parse.quote(artist)
    spotify = f"https://open.spotify.com/search/{quoted}"
    soundcloud = f"https://soundcloud.com/search?q={quoted}"
    return f"Spotify {spotify} · SoundCloud {soundcloud}"


def _fmt_times(event: dict) -> str:
    start = event.get("starts_at")
    end = event.get("ends_at")
    try:
        start_dt = datetime.fromisoformat(start) if start else None
    except ValueError:
        start_dt = None
    if start_dt is None:
        return "Time TBA"
    line = start_dt.strftime("%a %d %b, %H:%M")
    if end:
        try:
            line += " – " + datetime.fromisoformat(end).strftime("%H:%M")
        except ValueError:
            pass
    return line


def _maps_link(venue_name: str | None) -> str:
    query = urllib.parse.quote(f"{venue_name} Milano" if venue_name else "Milano")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def build_description(
    event: dict,
    features: dict,
    breakdown: dict,
    config: dict,
    affinity_meta: dict | None = None,
    bot_username: str | None = None,
) -> str:
    """Full human-readable description. Missing data (price, detail) degrades gracefully."""
    affinity_meta = affinity_meta or {}
    lines: list[str] = []

    kind = "Open-air" if features.get("is_open_air") else "Event"
    lines.append(f"{kind} · {_fmt_times(event)}")
    lines.append("")

    # Lineup with taste marks.
    import json as _json
    raw_names = _json.loads(event["lineup_raw"]) if event.get("lineup_raw") else []
    if raw_names:
        lines.append("Lineup:")
        matched = features.get("matched_artists", {})
        for raw in raw_names:
            mark = ""
            key = raw.strip().lower()
            # matched_artists is keyed by normalized name; do a light check.
            for norm in matched:
                if norm in key or key in norm:
                    meta = affinity_meta.get(norm, {})
                    if meta.get("source") == "lastfm_similar" and meta.get("origin"):
                        mark = f"  ≈ similar to {meta['origin']}"
                    else:
                        mark = "  ★ your taste"
                    break
            lines.append(f"  • {raw}{mark}")
            lines.append(f"      {_listen_links(raw)}")
        lines.append("")

    # Venue + map.
    venue = event.get("venue_name")
    lines.append(f"Venue: {venue or 'TBA'}")
    lines.append(f"Map: {_maps_link(venue)}")
    if event.get("url"):
        lines.append(f"RA: {event['url']}")
    lines.append("")

    # Price / tickets.
    if event.get("is_free"):
        lines.append("Price: free")
    elif event.get("price_min") is not None:
        lines.append(f"Price: from €{int(event['price_min'])}")
    else:
        lines.append("Price: see RA link (some Milan clubs need an ARCI tessera)")
    lines.append("")

    # Top-3 score factors.
    def _abs_contribution(item):
        return abs(item[1])

    top_factors = sorted(breakdown.items(), key=_abs_contribution, reverse=True)[:3]
    factor_text = ", ".join(f"{name} {value:+g}" for name, value in top_factors)
    lines.append(f"Why this score: {factor_text}")
    lines.append("")

    # Colour legend + feedback deeplinks.
    color_map = config.get("calendar", {}).get("color_feedback", {})
    if color_map:
        legend = " · ".join(f"{meta['label']}" for meta in color_map.values())
        lines.append(f"Recolour to rate: {legend}")
    if bot_username:
        # Telegram start params allow only [A-Za-z0-9_-], so use the numeric ra_id,
        # not the "ra:123" id. The bot reconstructs event_id = "ra:" + ra_id.
        ra_id = event.get("ra_id") or (event["id"].split(":", 1)[-1])
        for label in ("love", "meh", "nope"):
            deeplink = f"https://t.me/{bot_username}?start=fb_{ra_id}_{label}"
            lines.append(f"  {label}: {deeplink}")

    return "\n".join(lines)
