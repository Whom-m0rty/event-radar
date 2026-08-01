"""Command line interface (typer). Human-readable logging to stdout.

Only `fetch` and `stats` do real work so far; the rest are declared stubs so the
command surface is stable and later steps just fill them in (spec section 12).
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from pathlib import Path

import typer

from event_radar import db
from event_radar.config import load_config
from event_radar.sources.ra import ResidentAdvisor
from event_radar.storage import upsert_events

app = typer.Typer(add_completion=False, help="Event Radar — Milan electronic events.")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("event_radar")


def _not_implemented(name: str) -> None:
    typer.secho(f"`{name}` is not implemented yet (coming in a later step).", fg=typer.colors.YELLOW)
    raise typer.Exit(code=0)


@app.command()
def fetch(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be stored; write nothing."),
    resolve_area: bool = typer.Option(False, "--resolve-area", help="Re-resolve the city's RA area id live."),
    window_days: int = typer.Option(None, help="Override the look-ahead window from config."),
) -> None:
    """Fetch upcoming Milan events from Resident Advisor into SQLite."""
    config = load_config()
    source_cfg = config.section("source")
    city = source_cfg.get("city", "milan")
    days = window_days if window_days is not None else source_cfg.get("window_days", 60)

    contact = config.require_env("RA_CONTACT")
    ra = ResidentAdvisor(
        contact=contact,
        area_id=source_cfg.get("area_id"),
        user_agent_template=source_cfg.get("user_agent", "EventRadar/0.1 (+mailto:{contact})"),
        request_delay_seconds=source_cfg.get("request_delay_seconds", 2.0),
        cache_expire_hours=source_cfg.get("cache_expire_hours", 6.0),
        max_retries=source_cfg.get("max_retries", 4),
        cache_path=str(config.project_root / "http_cache"),
    )

    if resolve_area or ra.area_id is None:
        resolved = ra.resolve_area(source_cfg.get("area_search_term", city))
        if resolved is None:
            typer.secho(f"Could not resolve area for {city!r}.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        ra.area_id = resolved
        typer.echo(f"Resolved area {city!r} -> {resolved}")

    date_from = date.today()
    date_to = date_from + timedelta(days=days)
    typer.echo(f"Fetching {city} events {date_from} .. {date_to} (area {ra.area_id}) ...")
    events = ra.fetch(city, date_from.isoformat(), date_to.isoformat())
    typer.echo(f"Got {len(events)} events.")

    for event in events[:10]:
        free = "FREE" if event.is_free else ""
        air = "OPEN-AIR" if event.is_open_air else ""
        flags = " ".join(flag for flag in (free, air) if flag)
        lineup = ", ".join(event.lineup[:4]) or "(no lineup)"
        typer.echo(f"  [{event.starts_at}] {event.title} @ {event.venue_name} {flags}")
        typer.echo(f"      {lineup}")

    if dry_run:
        typer.secho("Dry run — nothing written.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    connection = db.connect(config.db_path)
    inserted, updated = upsert_events(connection, events)
    connection.close()
    typer.secho(f"Stored: {inserted} new, {updated} updated -> {config.db_path}", fg=typer.colors.GREEN)


@app.command()
def stats() -> None:
    """Show basic counts from the database."""
    config = load_config()
    connection = db.connect(config.db_path)
    events_count = connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    feedback_count = connection.execute("SELECT COUNT(*) AS n FROM feedback_events").fetchone()["n"]
    impressions_count = connection.execute("SELECT COUNT(*) AS n FROM impressions").fetchone()["n"]
    connection.close()
    typer.echo(f"events:      {events_count}")
    typer.echo(f"feedback:    {feedback_count}")
    typer.echo(f"impressions: {impressions_count}")


def _report_spotify_error(error) -> None:
    message = str(error)
    if "premium subscription required" in message.lower():
        typer.secho(
            "Spotify Web API is blocked: the app owner's account is not Premium.\n"
            "Options: upgrade the owner account to Premium, or switch the profile to "
            "seed mode (a manual artist list expanded via Last.fm).",
            fg=typer.colors.RED,
        )
    else:
        typer.secho(f"Spotify error: {message}", fg=typer.colors.RED)


def _handle_google_error(error, command: str) -> None:
    """On a Google auth/refresh failure, ping Telegram and print re-auth steps."""
    from event_radar.telegram.notify import notify_reauth

    message = str(error)
    looks_like_auth = (
        error.__class__.__name__ in ("RefreshError", "GoogleAuthError")
        or "invalid_grant" in message
        or "expired or revoked" in message
        or "Token has been" in message
    )
    if looks_like_auth:
        notify_reauth(command)
        typer.secho(
            "Google token expired (Testing-mode refresh tokens last ~7 days).\n"
            f"Re-run `{command}` and approve in the browser. (Pinged Telegram if configured.)",
            fg=typer.colors.RED,
        )
    else:
        typer.secho(f"Google Calendar error: {message}", fg=typer.colors.RED)


def _spotify_client(config, connection):
    from event_radar.profile.spotify import SpotifyClient

    return SpotifyClient(
        client_id=config.require_env("SPOTIFY_CLIENT_ID"),
        client_secret=config.require_env("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=config.env("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback"),
        connection=connection,
    )


@app.command(name="spotify-login")
def spotify_login() -> None:
    """One-time Spotify authorization (opens a local callback server)."""
    from event_radar.profile.spotify import run_login

    config = load_config()
    connection = db.connect(config.db_path)
    client = _spotify_client(config, connection)

    state = secrets.token_urlsafe(16)
    url = client.authorize_url(state)
    typer.secho("Open this URL in your browser and approve:", fg=typer.colors.CYAN)
    typer.echo(url)
    typer.echo("Waiting for the callback on http://127.0.0.1:8080/callback ...")

    from event_radar.profile.spotify import SpotifyAuthError

    run_login(client)
    typer.secho("Token stored.", fg=typer.colors.GREEN)
    try:
        me = client.me()
    except SpotifyAuthError as error:
        connection.close()
        _report_spotify_error(error)
        raise typer.Exit(code=1)
    connection.close()
    typer.secho(
        f"Authorized as {me.get('display_name')} "
        f"(account: {me.get('product')}, country: {me.get('country')}).",
        fg=typer.colors.GREEN,
    )


@app.command()
def profile(top: int = typer.Option(50, help="How many top artists to print.")) -> None:
    """Build the taste profile from Spotify (Last.fm expansion comes next)."""
    from event_radar.profile.build import build_spotify_affinity, persist_affinity

    config = load_config()
    connection = db.connect(config.db_path)
    client = _spotify_client(config, connection)

    from event_radar.profile.spotify import SpotifyAuthError

    if not client.has_token():
        typer.secho("No Spotify token — run `event-radar spotify-login` first.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        affinity = build_spotify_affinity(client, config.section("profile"))
    except SpotifyAuthError as error:
        connection.close()
        _report_spotify_error(error)
        raise typer.Exit(code=1)
    persist_affinity(connection, affinity, replace_sources=("spotify_top", "spotify_followed"))
    connection.close()

    def _weight_of(pair):
        return pair[1].weight

    ranked = sorted(affinity.items(), key=_weight_of, reverse=True)
    typer.echo(f"Taste profile: {len(affinity)} artists (top {min(top, len(ranked))}):")
    for name, entry in ranked[:top]:
        typer.echo(f"  {entry.weight:5.3f}  {name}   [{entry.source}]")


@app.command(name="profile-import")
def profile_import(
    html: str = typer.Option(None, help="Path to Spotify web-player tracklist HTML."),
    text: str = typer.Option(None, help="Path to a text file: one track per line, artists comma-separated."),
    top: int = typer.Option(50, help="How many artists to print."),
) -> None:
    """Build the seed taste profile from a Spotify web-player export (no API)."""
    from event_radar.profile.build import persist_affinity
    from event_radar.profile.spotify_html import (
        build_seed_affinity_from_db,
        parse_liked_html,
        parse_liked_text,
        upsert_seed_tracks,
    )

    if not html and not text:
        typer.secho("Give --html or --text with a file to import.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    config = load_config()
    connection = db.connect(config.db_path)

    if html:
        tracks = parse_liked_html(Path(html).read_text(encoding="utf-8"))
    else:
        tracks = parse_liked_text(Path(text).read_text(encoding="utf-8"))
    typer.echo(f"Parsed {len(tracks)} tracks from the import.")

    total_tracks = upsert_seed_tracks(connection, tracks)
    affinity = build_seed_affinity_from_db(connection, config.section("profile"))
    persist_affinity(connection, affinity, replace_sources=("spotify_liked",))
    connection.close()

    def _weight_of(pair):
        return pair[1].weight

    ranked = sorted(affinity.items(), key=_weight_of, reverse=True)
    typer.echo(f"Seed profile: {len(affinity)} artists from {total_tracks} liked tracks (top {min(top, len(ranked))}):")
    for name, entry in ranked[:top]:
        typer.echo(f"  {entry.weight:5.3f}  {name}")


@app.command()
def evaluate(k: int = typer.Option(50, help="Top-K for recall@K.")) -> None:
    """Leave-one-out recall@K / MRR on the taste library + coverage@events (step 4)."""
    from event_radar.evaluation.loo import coverage_at_events, leave_one_out
    from event_radar.profile.build import expand_with_lastfm, persist_affinity
    from event_radar.profile.lastfm import LastFm
    from event_radar.profile.spotify_html import build_seed_affinity_from_db

    config = load_config()
    profile_cfg = config.section("profile")
    lastfm_cfg = profile_cfg.get("lastfm", {})
    connection = db.connect(config.db_path)

    seed = build_seed_affinity_from_db(connection, profile_cfg)
    if not seed:
        typer.secho("No seed profile — run `profile-import` first.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    lastfm = LastFm(
        api_key=config.require_env("LASTFM_API_KEY"),
        cache_path=str(config.project_root / "lastfm_cache"),
        limit=lastfm_cfg.get("similar_limit", 20),
    )

    typer.echo(f"Leave-one-out over {len(seed)} seed artists (this fetches Last.fm once per artist, cached)...")
    seed_weights = {}
    for name, entry in seed.items():
        seed_weights[name] = entry.weight

    loo = leave_one_out(
        seed_weights,
        lastfm,
        expand_above=lastfm_cfg.get("expand_above", 0.5),
        decay=lastfm_cfg.get("decay", 0.4),
        k=k,
    )
    typer.secho(
        f"\nLAST.FM GRAPH QUALITY (does the graph reconstruct held-out favourites?)",
        fg=typer.colors.CYAN,
    )
    typer.echo(f"  recall@{loo['k']}: {loo['recall_at_k']:.3f}  ({loo['hits']}/{loo['held_out']} reconstructed)")
    typer.echo(f"  MRR:       {loo['mrr']:.3f}")

    # Build the full expanded profile and persist the Last.fm rows.
    merged = expand_with_lastfm(seed, lastfm, profile_cfg)
    lastfm_only = {}
    for name, entry in merged.items():
        if entry.source == "lastfm_similar":
            lastfm_only[name] = entry
    persist_affinity(connection, lastfm_only, replace_sources=("lastfm_similar",))
    typer.echo(f"\nProfile expanded: {len(seed)} seed + {len(lastfm_only)} Last.fm = {len(merged)} artists.")

    coverage = coverage_at_events(connection, set(merged.keys()))
    connection.close()

    typer.secho(f"\nCOVERAGE@EVENTS (does the profile touch actual Milan lineups?)", fg=typer.colors.CYAN)
    typer.echo(
        f"  events with any match: {coverage['events_with_any_match']}/{coverage['total_events']} "
        f"({coverage['event_coverage']:.1%})"
    )
    typer.echo(
        f"  lineup artists matched: {coverage['matched_lineup_artists']}/{coverage['total_lineup_artists']} "
        f"({coverage['artist_coverage']:.1%})"
    )


def _load_affinity(connection) -> dict:
    affinity = {}
    for row in connection.execute("SELECT artist_name_normalized, weight FROM affinity"):
        affinity[row["artist_name_normalized"]] = row["weight"]
    return affinity


@app.command()
def score(top: int = typer.Option(15, help="How many scored events to print.")) -> None:
    """Score fetched events with the config formula (music as a booster)."""
    import json as _json
    from datetime import datetime, timezone

    from event_radar.scoring.features import build_features
    from event_radar.scoring.score import score_event

    config = load_config()
    scoring_cfg = config.section("scoring")
    connection = db.connect(config.db_path)
    affinity = _load_affinity(connection)

    events = connection.execute("SELECT * FROM events").fetchall()
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("DELETE FROM scores")

    scored = []
    for event in events:
        features = build_features(dict(event), affinity)
        value, breakdown = score_event(features, scoring_cfg)
        connection.execute(
            "INSERT INTO scores (event_id, score, breakdown, computed_at) VALUES (?,?,?,?)",
            (event["id"], value, _json.dumps(breakdown), now),
        )
        scored.append((event, value, breakdown, features))
    connection.commit()
    connection.close()

    def _score_of(item):
        return item[1]

    scored.sort(key=_score_of, reverse=True)
    threshold = scoring_cfg.get("push_threshold", 35)
    above = [item for item in scored if item[1] >= threshold]
    typer.echo(f"Scored {len(scored)} events; {len(above)} above push threshold {threshold}.")
    for event, value, breakdown, features in scored[:top]:
        factors = ", ".join(f"{name}+{val}" if val >= 0 else f"{name}{val}"
                            for name, val in breakdown.items())
        matched = ", ".join(features["matched_artists"].keys())
        tag = "★" if features["matched_artists"] else " "
        typer.echo(f"  {value:5.1f} {tag} {event['title']}  @ {event['venue_name']}")
        typer.echo(f"          {factors}" + (f"  | music: {matched}" if matched else ""))


def _affinity_meta(connection) -> dict:
    meta = {}
    for row in connection.execute("SELECT artist_name_normalized, source, origin FROM affinity"):
        meta[row["artist_name_normalized"]] = {"source": row["source"], "origin": row["origin"]}
    return meta


def _scored_above_threshold(connection, scoring_cfg):
    from event_radar.scoring.features import build_features
    from event_radar.scoring.score import score_event

    affinity = _load_affinity(connection)
    threshold = scoring_cfg.get("push_threshold", 35)
    result = []
    for event in connection.execute("SELECT * FROM events").fetchall():
        event_dict = dict(event)
        features = build_features(event_dict, affinity)
        value, breakdown = score_event(features, scoring_cfg)
        if value >= threshold:
            result.append((event_dict, value, breakdown, features))

    def _score_of(item):
        return item[1]

    result.sort(key=_score_of, reverse=True)
    return result


@app.command(name="push-calendar")
def push_calendar(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be pushed; no Google calls."),
    limit: int = typer.Option(50, help="Max events to push."),
) -> None:
    """Push events above the score threshold to Google Calendar (idempotent)."""
    from event_radar.calendar.format import build_description, build_title

    config = load_config()
    scoring_cfg = config.section("scoring")
    connection = db.connect(config.db_path)
    meta = _affinity_meta(connection)
    bot_username = config.env("TELEGRAM_BOT_USERNAME")

    events = _scored_above_threshold(connection, scoring_cfg)[:limit]
    typer.echo(f"{len(events)} events above threshold {scoring_cfg.get('push_threshold', 35)}.")

    if dry_run:
        for event_dict, value, breakdown, features in events[:8]:
            title = build_title(event_dict, value)
            description = build_description(event_dict, features, breakdown, config.raw, meta, bot_username)
            typer.secho(f"\n{title}", fg=typer.colors.GREEN)
            typer.echo(description)
        connection.close()
        typer.secho("\nDry run — nothing pushed.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    from event_radar.calendar.gcal import GoogleCalendar

    credentials_file = config.env("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    gcal = GoogleCalendar(
        credentials_file=str(config.project_root / credentials_file),
        token_file=str(config.project_root / "token.json"),
        connection=connection,
    )
    try:
        calendar_id = gcal.ensure_calendar(config.section("calendar").get("name", "Event Radar"))
        pushed = 0
        for event_dict, value, breakdown, features in events:
            title = build_title(event_dict, value)
            description = build_description(event_dict, features, breakdown, config.raw, meta, bot_username)
            gcal.push_event(calendar_id, event_dict, title, description, value)
            pushed += 1
    except Exception as error:  # noqa: BLE001 — surface auth failure to Telegram
        connection.close()
        _handle_google_error(error, "event-radar push-calendar")
        raise typer.Exit(code=1)
    connection.close()
    typer.secho(f"Pushed {pushed} events to calendar {calendar_id}.", fg=typer.colors.GREEN)


@app.command()
def share(email: str = typer.Option(..., help="Gmail to share the calendar with (read-only).")) -> None:
    """Share the Event Radar calendar with a friend (read access)."""
    from event_radar.calendar.gcal import GoogleCalendar

    config = load_config()
    connection = db.connect(config.db_path)
    credentials_file = config.env("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    gcal = GoogleCalendar(
        credentials_file=str(config.project_root / credentials_file),
        token_file=str(config.project_root / "token.json"),
        connection=connection,
    )
    calendar_id = gcal.ensure_calendar(config.section("calendar").get("name", "Event Radar"))
    gcal.share(calendar_id, email)
    connection.close()
    typer.secho(f"Shared with {email} (read-only).", fg=typer.colors.GREEN)


@app.command(name="sync-feedback")
def sync_feedback() -> None:
    """Read calendar colours into feedback_events (append-only) + snapshots."""
    from event_radar.calendar.gcal import GoogleCalendar
    from event_radar.feedback.sync import sync_calendar_colors

    config = load_config()
    connection = db.connect(config.db_path)
    affinity = _load_affinity(connection)

    credentials_file = config.env("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    gcal = GoogleCalendar(
        credentials_file=str(config.project_root / credentials_file),
        token_file=str(config.project_root / "token.json"),
        connection=connection,
    )
    try:
        calendar_id = gcal.ensure_calendar(config.section("calendar").get("name", "Event Radar"))
        calendar_events = gcal.list_events(calendar_id)
    except Exception as error:  # noqa: BLE001 — surface auth failure to Telegram
        connection.close()
        _handle_google_error(error, "event-radar sync-feedback")
        raise typer.Exit(code=1)
    written = sync_calendar_colors(connection, calendar_events, affinity, config.raw)

    total = connection.execute("SELECT COUNT(*) AS n FROM feedback_events").fetchone()["n"]
    connection.close()
    typer.secho(f"Synced colours: {written} new feedback rows this run ({total} total).", fg=typer.colors.GREEN)


@app.command(name="export-feedback")
def export_feedback(
    format: str = typer.Option("jsonl", help="jsonl or parquet."),
    out: str = typer.Option("exports/feedback.jsonl", help="Output path for feedback."),
    impressions_out: str = typer.Option("exports/impressions.jsonl", help="Output path for impressions."),
) -> None:
    """Export feedback (with snapshots) + impressions as a self-contained dataset."""
    from event_radar.export.dump import export_feedback as _export_feedback
    from event_radar.export.dump import export_impressions as _export_impressions

    config = load_config()
    connection = db.connect(config.db_path)
    n_feedback = _export_feedback(connection, out, format)
    n_impressions = _export_impressions(connection, impressions_out, format)
    connection.close()
    typer.secho(f"Exported {n_feedback} feedback rows -> {out}", fg=typer.colors.GREEN)
    typer.secho(f"Exported {n_impressions} impressions -> {impressions_out}", fg=typer.colors.GREEN)


@app.command()
def pipeline(
    window_days: int = typer.Option(None, help="Override look-ahead window."),
    skip_calendar: bool = typer.Option(False, help="Fetch+score only, no Google calls."),
) -> None:
    """Full cron pipeline: fetch -> score -> push-calendar -> sync-feedback."""
    from event_radar.pipeline import calendar_step, fetch_step, score_step

    config = load_config()
    connection = db.connect(config.db_path)

    inserted, updated = fetch_step(config.raw, connection, config.require_env("RA_CONTACT"), window_days)
    typer.echo(f"fetch: {inserted} new, {updated} updated")
    scored = score_step(config.raw, connection)
    typer.echo(f"score: {scored} events")

    if skip_calendar:
        connection.close()
        typer.secho("Done (calendar skipped).", fg=typer.colors.GREEN)
        return

    from event_radar.calendar.gcal import GoogleCalendar

    credentials_file = config.env("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    gcal = GoogleCalendar(
        credentials_file=str(config.project_root / credentials_file),
        token_file=str(config.project_root / "token.json"),
        connection=connection,
    )
    try:
        pushed, written = calendar_step(config.raw, connection, gcal, config.env("TELEGRAM_BOT_USERNAME"))
    except Exception as error:  # noqa: BLE001 — surface auth failure to Telegram
        connection.close()
        _handle_google_error(error, "event-radar pipeline")
        raise typer.Exit(code=1)
    connection.close()
    typer.secho(f"calendar: pushed {pushed}, new feedback {written}. Done.", fg=typer.colors.GREEN)


@app.command()
def bot() -> None:
    """Run the Telegram bot (long-polling)."""
    from event_radar.telegram.bot import run

    config = load_config()
    token = config.require_env("TELEGRAM_BOT_TOKEN")
    chat_id = config.env("TELEGRAM_CHAT_ID")
    run(token, config.raw, config.db_path, chat_id)


if __name__ == "__main__":
    app()
