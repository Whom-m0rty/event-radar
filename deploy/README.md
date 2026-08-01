# Deploy

State lives in **Turso** (hosted libSQL), so the pipeline and the bot can run in
different places and share one DB. Two runtimes:

- **Pipeline** (fetch→score→push→sync, twice daily) — runs wherever; simplest is
  **launchd on a Mac** (below). Set `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` and
  it reads/writes the cloud DB.
- **Bot** (24/7 webhook) — Cloudflare Worker (see the worker/ setup, phase 3).

The Google OAuth token is stored **in Turso** (`app_state.google_token`), so a
stateless runner refreshes it without a local file. Do the first browser consent
once locally (`event-radar spotify-login` for Spotify / `push-calendar` for
Google), which seeds the token into Turso.

## Pipeline via launchd (macOS)

```bash
cp deploy/com.eventradar.pipeline.plist.template ~/Library/LaunchAgents/com.eventradar.pipeline.plist
# edit it: your uv path, project path, and the env values
chmod 600 ~/Library/LaunchAgents/com.eventradar.pipeline.plist
launchctl load -w ~/Library/LaunchAgents/com.eventradar.pipeline.plist
launchctl start com.eventradar.pipeline           # run once now
tail -f /tmp/event-radar-pipeline.out.log
```

Runs at 09:00 and 20:00 (only while the Mac is awake). To stop:
`launchctl unload ~/Library/LaunchAgents/com.eventradar.pipeline.plist`.

## Pipeline via GitHub Actions (alternative)

`.github/workflows/pipeline.yml` runs the same pipeline on a cron. Set the repo
secrets (TURSO_*, RA_CONTACT, LASTFM_API_KEY, TELEGRAM_*, GOOGLE_CREDENTIALS_JSON)
and uncomment the `schedule:` trigger. Needs a GitHub account in good standing
(Actions is free for public repos).

---

# Appendix: Oracle Cloud Always Free (24/7 VM, alternative to launchd)

Runs the **pipeline** (fetch→score→push→sync, twice daily via a systemd timer)
and the **bot** (long-polling, systemd service). SQLite lives on the VM disk.
No inbound ports are needed — everything is outbound (Telegram/Google/RA).

## 1. Create the VM
- Oracle Cloud → Compute → Instance → shape **VM.Standard.A1.Flex** (Ampere ARM,
  *Always Free*), image **Ubuntu 22.04**. 1 OCPU / 6 GB is plenty.
- No ingress rules required. SSH in.

## 2. Provision
```bash
sudo adduser --system --group --home /opt/event-radar eventradar
sudo apt update && sudo apt install -y git
# uv for the eventradar user
sudo -u eventradar bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# code
sudo -u eventradar git clone https://github.com/Whom-m0rty/event-radar.git /opt/event-radar
sudo -u eventradar bash -lc 'cd /opt/event-radar && uv sync'
# system clock -> Rome (the timer + scoring assume it)
sudo timedatectl set-timezone Europe/Rome
```

## 3. Copy state from your Mac (important)
The SQLite DB holds your **taste profile, the calendar id, and the sync/feedback
history** — copy it so the VM reuses the same calendar instead of making a new
one. `token.json` is your Google auth (first consent was done on the Mac).

```bash
# from your Mac, in ~/PycharmProjects/event-radar
scp event_radar.db credentials.json token.json \
    eventradar@<vm-ip>:/opt/event-radar/
```

## 4. Secrets
```bash
sudo mkdir -p /etc/event-radar
sudo cp /opt/event-radar/deploy/env.example /etc/event-radar/env
sudo nano /etc/event-radar/env      # fill RA_CONTACT, LASTFM_API_KEY, TELEGRAM_*
sudo chmod 600 /etc/event-radar/env
```

## 5. Install services
```bash
sudo cp /opt/event-radar/deploy/event-radar-*.service /etc/systemd/system/
sudo cp /opt/event-radar/deploy/event-radar-pipeline.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now event-radar-bot.service
sudo systemctl enable --now event-radar-pipeline.timer
# run the pipeline once now:
sudo systemctl start event-radar-pipeline.service
journalctl -u event-radar-pipeline.service -n 40 --no-pager
```

## 6. Re-auth (the weekly Google-token dance)
The OAuth consent screen is in **Testing**, so Google expires the refresh token
after ~7 days. When that happens the pipeline **pings your Telegram** ("Google
authorization expired…"). It's headless here, so re-auth on your **Mac**:

```bash
# on the Mac
GOOGLE_CREDENTIALS_FILE=credentials.json uv run event-radar push-calendar --limit 1
# approve in the browser, then copy the fresh token up:
scp token.json eventradar@<vm-ip>:/opt/event-radar/
```

(Or publish the OAuth app to Production once — one click — and the refresh token
stops expiring; then you never do this again.)

## Checks
```bash
systemctl status event-radar-bot.service
systemctl list-timers event-radar-pipeline.timer
journalctl -u event-radar-bot.service -f
```
