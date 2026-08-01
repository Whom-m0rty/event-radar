# Event Radar

Personal aggregator of electronic events in Milan. It fetches the listings,
scores each event against your Spotify taste with a transparent formula (no ML),
pushes the good ones to a shared Google Calendar, and collects your feedback in a
portable, append-only store.

> Status: **all steps 1–9 built** — fetch (RA), taste profile (seed from Spotify
> web export + Last.fm expansion), `evaluate`, scoring (music as a booster),
> Google Calendar push/share, colour feedback sync, export, and the Telegram bot.
> The bot is a long-lived local process — run it in your own terminal.

## Run the Telegram bot

```bash
export TELEGRAM_BOT_TOKEN=...          # from @BotFather
export TELEGRAM_BOT_USERNAME=...       # bot username without @ (for calendar deeplinks)
export TELEGRAM_CHAT_ID=...            # optional: group chat for the daily auto-digest
uv run event-radar bot
```

Then DM the bot `/digest`, or add it to a group. `/today` `/weekend` `/free`
`/score ra:<id>` `/calendar` `/stats` also work; inline buttons and description
deeplinks record feedback; a daily digest and a 24h post-event survey run on a
schedule when `TELEGRAM_CHAT_ID` is set.

## Setup

```bash
uv sync
cp .env.example .env      # then fill it in
```

Minimum to run `fetch`: set `RA_CONTACT` in `.env` (an email that goes into the
honest User-Agent RA sees).

## Commands

```bash
uv run event-radar fetch --dry-run     # show what would be stored, write nothing
uv run event-radar fetch               # fetch Milan events into event_radar.db
uv run event-radar stats               # counts
```

Everything else (`profile`, `evaluate`, `score`, `push-calendar`,
`sync-feedback`, `export-feedback`, `bot`) is stubbed until its step lands.

## Data source

Resident Advisor has no public API; we use the same undocumented GraphQL endpoint
its site uses, politely: honest UA + contact, ≥2s between requests, retries with
backoff, on-disk response cache. Milan is RA area **347** (resolved live via the
`areas(searchTerm)` query, cached in `config.yaml`). The listing has no price —
that comes from a later per-event detail pass.

## Configuration

Non-secret knobs (scoring weights, thresholds, colour→feedback map) live in
`config.yaml`. Secrets live only in `.env` and are never committed.

## Your data

Feedback is the point of the whole system, so it exports to a self-contained file
(one row = one labelled fact, with the feature snapshot, score, breakdown and the
taste profile from that moment glued in). It opens with no access to the DB.

```bash
uv run event-radar export-feedback --format jsonl --out exports/feedback.jsonl
```

Look at it with pandas:

```python
import pandas as pd, json

rows = [json.loads(line) for line in open("exports/feedback.jsonl")]
df = pd.json_normalize(rows)

# how many labels, and the class distribution per dimension
print(df.groupby(["dimension", "label"]).size())

# intent vs experience for the same events — the disagreement is the interesting part
intent = df[df.dimension == "intent"][["event_id", "label"]]
experience = df[df.dimension == "experience"][["event_id", "label"]]
print(intent.merge(experience, on="event_id", suffixes=("_intent", "_exp")))

# which features correlate with a positive label
df["liked"] = df["label"].isin(["love", "going", "great", "ok"])
print(df[["snapshot.score", "liked"]].corr())
```

Impressions export separately (`exports/impressions.jsonl`) — needed to tell
"didn't like it" from "never saw it".

## Tests

```bash
uv run pytest
```

Network sources are tested against saved fixtures — no live requests in tests.
