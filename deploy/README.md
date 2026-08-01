# Deploy on Oracle Cloud Always Free (24/7, free forever)

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
