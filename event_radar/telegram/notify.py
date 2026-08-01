"""Fire-and-forget Telegram notifications (e.g. "your Google token died, re-auth").

Used by the cron pipeline: OAuth consent stays in Testing mode (refresh token
expires ~weekly), so instead of publishing to Production we just ping the chat
when a Google call fails, and re-auth is done by hand.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_telegram(token: str | None, chat_id: str | None, text: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        return response.status_code == 200
    except requests.RequestException as error:
        logger.warning("Telegram notify failed: %s", error)
        return False


def notify_reauth(command: str) -> None:
    """Tell the chat that Google needs re-authorizing (reads token/chat from env)."""
    send_telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN"),
        os.environ.get("TELEGRAM_CHAT_ID"),
        f"⚠️ Event Radar: Google authorization expired.\n"
        f"Re-run `{command}` on the host and approve in the browser once.",
    )
