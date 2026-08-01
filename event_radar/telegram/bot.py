"""Telegram bot (spec section 10). Group chat, long-polling.

Commands: /digest /today /weekend /free /score /calendar /stats, inline feedback
buttons under each digested event, feedback deeplinks (/start fb_<raid>_<label>),
a daily auto-digest and a 24h post-event survey via the JobQueue.

DB access is plain sqlite3 opened per handler (fast, local). Scoring is computed
live from the current profile so the digest always reflects the latest taste.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from event_radar import db
from event_radar.scoring.features import build_features
from event_radar.scoring.score import score_event
from event_radar.telegram.feedback_actions import apply_feedback

logger = logging.getLogger(__name__)

# Inline keyboard: emoji -> label. love/meh/nope/going are the intent scale.
_INTENT_BUTTONS = [("🔥", "love"), ("😐", "meh"), ("👎", "nope"), ("🎟 Иду", "going")]
_EXPERIENCE_BUTTONS = [("🔥 огонь", "great"), ("👌 норм", "ok"), ("👎 не зашло", "bad"), ("🚫 не пошёл", "didnt_go")]


def _conn(context):
    return db.connect(context.application.bot_data["db_path"])


def _config(context):
    return context.application.bot_data["config"]


def _affinity(connection):
    affinity = {}
    for row in connection.execute("SELECT artist_name_normalized, weight FROM affinity"):
        affinity[row["artist_name_normalized"]] = row["weight"]
    return affinity


def _score_events(connection, scoring_cfg):
    affinity = _affinity(connection)
    scored = []
    for event in connection.execute("SELECT * FROM events").fetchall():
        event_dict = dict(event)
        features = build_features(event_dict, affinity)
        value, breakdown = score_event(features, scoring_cfg)
        scored.append((event_dict, value, breakdown, features))

    def _score_of(item):
        return item[1]

    scored.sort(key=_score_of, reverse=True)
    return scored


def _keyboard(event_id: str, buttons) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text, callback_data=f"fb|{event_id}|{label}") for text, label in buttons]
    return InlineKeyboardMarkup([row])


def _event_line(event: dict, value: float, features: dict) -> str:
    star = " ★" if features.get("matched_artists") else ""
    price = "free" if event.get("is_free") else "€?"
    when = event.get("starts_at", "")[:16].replace("T", " ")
    return f"*[{int(round(value))}]{star}* {event.get('title')}\n{when} · {event.get('venue_name')} · {price}"


def _log_impression(connection, event_id, value, surface):
    connection.execute(
        "INSERT INTO impressions (event_id, user_id, surface, score_at_show, shown_at) "
        "VALUES (?, 'owner', ?, ?, ?)",
        (event_id, surface, value, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


# -- commands -------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if args and args[0].startswith("fb_"):
        # fb_<raid>_<label> -> reconstruct "ra:<raid>"
        parts = args[0].split("_")
        if len(parts) == 3:
            _prefix, ra_id, label = parts
            event_id = f"ra:{ra_id}"
            connection = _conn(context)
            ok = apply_feedback(
                connection, event_id, str(update.effective_user.id), label, "deeplink",
                _affinity(connection), _config(context).get("scoring", {}),
            )
            connection.close()
            await update.message.reply_text("Отметил ✅" if ok else "Событие не найдено 🤔")
            return
    await update.message.reply_text(
        "Event Radar. /digest — топ событий, /today /weekend /free, /calendar, /stats."
    )


async def digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connection = _conn(context)
    config = _config(context)
    threshold = config.get("scoring", {}).get("push_threshold", 35)
    size = config.get("telegram", {}).get("digest_size", 10)
    scored = [item for item in _score_events(connection, config.get("scoring", {})) if item[1] >= threshold]

    if not scored:
        await update.message.reply_text("Пока нечего показать — запусти fetch/score.")
        connection.close()
        return

    for event, value, _breakdown, features in scored[:size]:
        _log_impression(connection, event["id"], value, "tg_digest")
        await update.message.reply_text(
            _event_line(event, value, features),
            parse_mode="Markdown",
            reply_markup=_keyboard(event["id"], _INTENT_BUTTONS),
        )
    connection.close()


async def _filtered(update, context, where: str, params=()):
    connection = _conn(context)
    config = _config(context)
    scored = _score_events(connection, config.get("scoring", {}))
    picked = []
    for event, value, _b, features in scored:
        if where == "today":
            starts = event.get("starts_at", "")
            if starts[:10] == datetime.now().strftime("%Y-%m-%d"):
                picked.append((event, value, features))
        elif where == "weekend":
            wd = features.get("weekday")
            if wd in (4, 5, 6):
                picked.append((event, value, features))
        elif where == "free":
            if event.get("is_free"):
                picked.append((event, value, features))
    if not picked:
        await update.message.reply_text("Ничего не нашлось.")
        connection.close()
        return
    for event, value, features in picked[:10]:
        await update.message.reply_text(
            _event_line(event, value, features), parse_mode="Markdown",
            reply_markup=_keyboard(event["id"], _INTENT_BUTTONS),
        )
    connection.close()


async def today(update, context):
    await _filtered(update, context, "today")


async def weekend(update, context):
    await _filtered(update, context, "weekend")


async def free(update, context):
    await _filtered(update, context, "free")


async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /score ra:123")
        return
    event_id = context.args[0]
    connection = _conn(context)
    config = _config(context)
    event = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        await update.message.reply_text("Событие не найдено.")
        connection.close()
        return
    features = build_features(dict(event), _affinity(connection))
    value, breakdown = score_event(features, config.get("scoring", {}))
    connection.close()
    factors = "\n".join(f"  {name}: {val:+g}" for name, val in breakdown.items())
    await update.message.reply_text(f"*{event['title']}* — {value}\n{factors}", parse_mode="Markdown")


async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connection = _conn(context)
    row = connection.execute("SELECT value FROM app_state WHERE key = 'calendar_id'").fetchone()
    connection.close()
    if row is None:
        await update.message.reply_text("Календарь ещё не создан.")
        return
    calendar_id = row["value"]
    await update.message.reply_text(
        f"Календарь: https://calendar.google.com/calendar/embed?src={calendar_id}\n"
        f"iCal: https://calendar.google.com/calendar/ical/{calendar_id}/public/basic.ics"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connection = _conn(context)
    events = connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    rows = connection.execute(
        "SELECT dimension, channel, COUNT(*) AS n FROM feedback_events GROUP BY dimension, channel"
    ).fetchall()
    connection.close()
    lines = [f"events: {events}"]
    for row in rows:
        lines.append(f"feedback {row['dimension']}/{row['channel']}: {row['n']}")
    await update.message.reply_text("\n".join(lines))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _prefix, event_id, label = query.data.split("|")
    except ValueError:
        return
    connection = _conn(context)
    apply_feedback(
        connection, event_id, str(query.from_user.id), label, "tg_button",
        _affinity(connection), _config(context).get("scoring", {}),
    )
    connection.close()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Записал: {label} ✅")


# -- scheduled jobs -------------------------------------------------------


async def _daily_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.application.bot_data.get("chat_id")
    if not chat_id:
        return
    connection = db.connect(context.application.bot_data["db_path"])
    config = context.application.bot_data["config"]
    threshold = config.get("scoring", {}).get("push_threshold", 35)
    scored = [i for i in _score_events(connection, config.get("scoring", {})) if i[1] >= threshold]
    for event, value, _b, features in scored[: config.get("telegram", {}).get("digest_size", 10)]:
        _log_impression(connection, event["id"], value, "tg_digest")
        await context.bot.send_message(
            chat_id, _event_line(event, value, features), parse_mode="Markdown",
            reply_markup=_keyboard(event["id"], _INTENT_BUTTONS),
        )
    connection.close()


async def _post_event_survey_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask, once, about events that ended ~1 day ago and had any positive intent."""
    chat_id = context.application.bot_data.get("chat_id")
    if not chat_id:
        return
    connection = db.connect(context.application.bot_data["db_path"])
    now = datetime.now()
    window_start = (now - timedelta(hours=48)).isoformat()
    window_end = (now - timedelta(hours=20)).isoformat()
    query = (
        "SELECT DISTINCT e.id, e.title FROM events e "
        "JOIN feedback_events f ON f.event_id = e.id "
        "WHERE f.dimension = 'intent' AND f.label IN ('love','going') "
        "AND e.starts_at BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM feedback_events x WHERE x.event_id = e.id AND x.dimension = 'experience')"
    )
    for row in connection.execute(query, (window_start, window_end)):
        await context.bot.send_message(
            chat_id,
            f"Был вчера на *{row['title']}*?",
            parse_mode="Markdown",
            reply_markup=_keyboard(row["id"], _EXPERIENCE_BUTTONS),
        )
    connection.close()


# -- wiring ---------------------------------------------------------------


def build_application(token: str, config: dict, db_path: str, chat_id: str | None = None) -> Application:
    application = Application.builder().token(token).build()
    application.bot_data["config"] = config
    application.bot_data["db_path"] = db_path
    application.bot_data["chat_id"] = chat_id

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("digest", digest))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("weekend", weekend))
    application.add_handler(CommandHandler("free", free))
    application.add_handler(CommandHandler("score", score_cmd))
    application.add_handler(CommandHandler("calendar", calendar_cmd))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(on_button, pattern=r"^fb\|"))

    if application.job_queue is not None:
        digest_time = config.get("telegram", {}).get("digest_time", "10:00")
        hour, minute = (int(part) for part in digest_time.split(":"))
        from datetime import time as dtime
        import zoneinfo
        rome = zoneinfo.ZoneInfo("Europe/Rome")
        application.job_queue.run_daily(_daily_digest_job, time=dtime(hour, minute, tzinfo=rome))
        application.job_queue.run_daily(_post_event_survey_job, time=dtime(11, 0, tzinfo=rome))

    return application


def run(token: str, config: dict, db_path: str, chat_id: str | None = None) -> None:
    application = build_application(token, config, db_path, chat_id)
    logger.info("Bot polling…")
    application.run_polling()
