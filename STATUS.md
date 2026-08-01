# Event Radar — Status & Roadmap

Personal aggregator of electronic events in Milan: fetch the listings, score them
against your Spotify taste with a transparent formula (no ML), push the good ones
to a shared Google Calendar, and collect feedback that accumulates for a future
model. **v1 is built, deployed, and runs autonomously.**

---

## Architecture (deployed)

```
                    ┌──────────── Turso (hosted libSQL, free) ───────────┐
                    │  single source of truth: profile, genre tags,      │
                    │  scores, calendar_id, Google/Spotify tokens,       │
                    │  feedback (+ snapshots)                             │
                    └──────▲──────────────────────────────────▲──────────┘
                           │ read/write                        │ read/write
     launchd on Mac (2×/day) ┘                                 └ Cloudflare Worker (24/7)
   fetch → genres → score →                                    webhook bot: /digest, /free,
   push-calendar → sync-feedback                               /stats, /calendar, deeplinks,
   → backfill snapshots                                        inline feedback buttons,
        │                                                       daily auto-digest (cron 10:00)
   Google Calendar "Event Radar — Milano"                           │
                                                              Telegram @MaksimEventBot
```

- **Pipeline** runs on a **macOS launchd agent** at 09:00 and 20:00 (only while the
  Mac is awake). State is in Turso, so it's cloud-shared with the bot.
- **Bot** is a **Cloudflare Worker** (webhook, not polling) — genuinely 24/7,
  independent of the Mac. It's *thin*: reads pre-computed `scores` and writes
  feedback; the pipeline back-fills feature snapshots for bot-written feedback.
- **Free everywhere**: Turso free tier, Cloudflare Workers free tier, Mac for the
  cron. (GitHub Actions was set up too but the account is billing-locked.)

## Where things live

| Thing | Where |
|---|---|
| Code | GitHub `Whom-m0rty/event-radar` (public) |
| DB | Turso (account `whom777`), db `event-radar`, region eu-west-1 |
| Bot | Cloudflare Worker `event-radar-bot` (`*.workers.dev`) |
| Telegram | bot `@MaksimEventBot` (your DM chat id is a Worker/launchd secret) |
| Calendar | Google "Event Radar — Milano" (id in Turso `app_state.calendar_id`) |
| Google OAuth | Desktop client, consent screen **Published (Production)** → token non-expiring |
| Pipeline runner | `~/Library/LaunchAgents/com.eventradar.pipeline.plist` |

**Secrets (never in the repo):** Turso auth token, Google token JSON, Spotify
token → in **Turso** (`app_state`/`oauth_tokens`). Worker secrets (`wrangler secret`).
launchd plist env (chmod 600). GitHub Actions secrets. `credentials.json` /
`token.json` / `event_radar.db` / `.turso_replica.*` are gitignored.

## Operating it

```bash
# --- worker (bot) ---
cd worker && npx wrangler deploy            # redeploy after editing src/index.ts
npx wrangler tail event-radar-bot           # live logs
npx wrangler secret put NAME                # set a secret
#   one-shot: GET https://<worker>/setup/<WEBHOOK_SECRET>  -> (re)set Telegram webhook
#   test:     GET https://<worker>/digest-now/<WEBHOOK_SECRET> -> send digest to your DM

# --- pipeline (Mac) ---
launchctl start com.eventradar.pipeline     # run once now
tail -f /tmp/event-radar-pipeline.out.log
launchctl unload ~/Library/LaunchAgents/com.eventradar.pipeline.plist   # stop

# --- run any command against Turso locally ---
export TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=...   # or leave unset -> local sqlite
uv run event-radar pipeline | score | push-calendar | sync-feedback | genres | profile-import

# --- Turso shell ---
~/.turso/turso db shell event-radar "SELECT COUNT(*) FROM feedback_events"

# --- re-auth Google (only if the token is ever revoked) ---
~/.turso/turso db shell event-radar "DELETE FROM app_state WHERE key='google_token'"
GOOGLE_CREDENTIALS_FILE=credentials.json uv run event-radar push-calendar --limit 1  # browser once

# --- tests ---
uv run pytest        # 52 tests
```

## Key decisions & gotchas (context for later)

- **Scoring = transparent formula, no ML** (deliberate). `base + practical factors
  (free/open-air/weekend/cheap/venue) + music booster + genre booster`, all weights
  in `config.yaml`, `breakdown` stored per score.
- **Genre matching is the important win.** Exact-artist matching gave **0%**
  coverage on RA-Milan (measured via `evaluate`): your artists and the lineup never
  share a name. Tagging both sides with Last.fm genres lifted it to **~55%** — this
  is why Anyma / Carl Cox / house nights surface. Built in `profile/genres.py`.
- **RA**: GraphQL endpoint only (HTML pages 403 our UA); Milan = area **347**; price
  comes from the free-text `cost` field (parsed). robots.txt blocks Claude/anthropic
  bots specifically, so the *tool* fetches under an honest `EventRadar` UA, not me.
- **Spotify Web API needs the app OWNER to be Premium** (new gate) — you're Free, so
  the profile is seeded from your **Liked-songs web export** (`profile-import`) and
  expanded via **Last.fm** `getSimilar`. `related-artists` is dead for new apps.
- **Google token in Turso, not a file**, so a stateless runner can refresh it.
  Consent screen Published → token doesn't expire.
- **Worker is thin on purpose** — no scoring/normalisation in TS; it reads
  precomputed scores, and the pipeline back-fills snapshots.
- **Push protection once caught a Google-token leak** in a libSQL `.db-wal` replica
  file — all replica artifacts are now gitignored. Watch for this.

---

## Roadmap / TODO (pick up here)

**Scoring (make recommendations sharper):**
- [ ] `attending` factor — RA gives headcount; your taste is festival-scale, so a
      mild bonus for big events would surface marquee shows. (fetch it → store → weight)
- [ ] Genre profile is currently built from all affinity (rap/hip-hop dominate from
      your Liked). Build it **seed-only** or bias toward electronic tags for a
      sharper match to the local techno/house scene.
- [ ] Feedback-driven weight calibration once ~30–50 labels exist (hand-tune from
      breakdowns; later a logistic model on the snapshots — no train/serve skew).
- [ ] Venue affinity learned from feedback (vs static whitelist).
- [ ] Exploration (ε-greedy): occasionally show a lower-scored/off-genre event so
      the formula gets feedback on what it wouldn't otherwise surface.

**Sources:**
- [ ] Add Dice.fm / Songkick / Bandsintown — your taste is broader than RA's techno
      focus. `sources/base.py` interface is ready for a drop-in.

**Bot:**
- [ ] Post-event survey (24h after a positive intent) — implemented in the Python
      bot (`telegram/bot.py`) but **not yet in the Worker**. Add as a second cron.
- [ ] Add bot to a group + set group `TELEGRAM_CHAT_ID` to share the digest.
- [ ] `/today` `/weekend` `/score <id>` in the Worker (only `/digest /free /stats
      /calendar` so far).

**Data / eval:**
- [ ] `export-feedback` + the README pandas section once labels accumulate
      (intent vs experience disagreement is the interesting signal).
- [ ] RSVP feedback channel (attendees[].responseStatus) — untested; colour channel
      is owner-only.

**Ops:**
- [ ] If GitHub billing is unlocked: uncomment the `schedule:` in
      `.github/workflows/pipeline.yml` (secrets already set) to run the pipeline in
      the cloud instead of the Mac.

## Verify it's alive
- `uv run pytest` → 52 passing
- DM `@MaksimEventBot` `/digest` → next 7 days, genre-scored, with buttons
- Google Calendar "Event Radar — Milano" → titles like `[73] Anyma`
