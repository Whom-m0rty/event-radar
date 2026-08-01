// Event Radar Telegram bot — Cloudflare Worker (webhook, not polling).
//
// Thin by design: it reads PRE-COMPUTED scores from Turso (the Python pipeline
// does all the heavy lifting) and writes feedback back (append-only). Feature
// snapshots for bot-written feedback are back-filled by the pipeline, so no
// scoring/normalisation logic needs to live here.

import { createClient, type Client } from "@libsql/client/web";

interface Env {
  TURSO_DATABASE_URL: string;
  TURSO_AUTH_TOKEN: string;
  TELEGRAM_BOT_TOKEN: string;
  WEBHOOK_SECRET: string;
  TELEGRAM_CHAT_ID?: string;
  PUSH_THRESHOLD?: string;
  DIGEST_SIZE?: string;
}

const LABEL_DIMENSION: Record<string, string> = {
  love: "intent", going: "intent", meh: "intent", nope: "intent",
  great: "experience", ok: "experience", bad: "experience", didnt_go: "experience",
};

function db(env: Env): Client {
  return createClient({ url: env.TURSO_DATABASE_URL, authToken: env.TURSO_AUTH_TOKEN });
}

async function tg(env: Env, method: string, body: unknown): Promise<any> {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.json();
}

function intentKeyboard(eventId: string) {
  return {
    inline_keyboard: [[
      { text: "🔥", callback_data: `fb|${eventId}|love` },
      { text: "😐", callback_data: `fb|${eventId}|meh` },
      { text: "👎", callback_data: `fb|${eventId}|nope` },
      { text: "🎟 Иду", callback_data: `fb|${eventId}|going` },
    ]],
  };
}

// Rich, calendar-like text (plain — no Markdown, so odd titles never break it).
function fmtEvent(row: any): string {
  const when = String(row.starts_at ?? "").slice(0, 16).replace("T", " ");
  const price = row.is_free ? "free" : (row.price_min != null ? `€${Math.round(Number(row.price_min))}` : "€?");
  let lineup = "";
  try {
    const artists = JSON.parse(row.lineup_raw || "[]");
    if (artists.length) lineup = "\n🎧 " + artists.slice(0, 5).join(", ");
  } catch { /* ignore */ }
  let factors = "";
  try {
    const breakdown = JSON.parse(row.breakdown || "{}") as Record<string, number>;
    const top = Object.entries(breakdown)
      .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])))
      .slice(0, 3)
      .map(([k, v]) => `${k} ${Number(v) >= 0 ? "+" : ""}${v}`);
    if (top.length) factors = "\n⭐ " + top.join(", ");
  } catch { /* ignore */ }
  const link = row.url ? `\n🔗 ${row.url}` : "";
  return `[${Math.round(Number(row.score))}] ${row.title}\n📅 ${when} · ${row.venue_name} · ${price}${lineup}${factors}${link}`;
}

async function sendDigest(env: Env, client: Client, chatId: number | string, extraWhere = ""): Promise<void> {
  const threshold = Number(env.PUSH_THRESHOLD ?? "35");
  const size = Number(env.DIGEST_SIZE ?? "10");
  const days = Number(env.DIGEST_DAYS ?? "7");

  // Next `days` window, sorted chronologically ("what's coming up").
  const now = new Date();
  const lo = now.toISOString().slice(0, 19);
  const hi = new Date(now.getTime() + days * 86400000).toISOString().slice(0, 19);

  const sql =
    `SELECT e.id, e.title, e.venue_name, e.starts_at, e.is_free, e.price_min, e.url, e.lineup_raw, ` +
    `s.score, s.breakdown FROM events e JOIN scores s ON s.event_id = e.id ` +
    `WHERE s.score >= ? AND e.starts_at >= ? AND e.starts_at <= ? ${extraWhere} ` +
    `ORDER BY e.starts_at ASC LIMIT ?`;
  const rs = await client.execute({ sql, args: [threshold, lo, hi, size] });
  if (rs.rows.length === 0) {
    await tg(env, "sendMessage", { chat_id: chatId, text: `Ближайшие ${days} дней — ничего выше порога.` });
    return;
  }
  const nowIso = now.toISOString();
  for (const row of rs.rows as any[]) {
    await tg(env, "sendMessage", {
      chat_id: chatId, text: fmtEvent(row),
      reply_markup: intentKeyboard(String(row.id)),
    });
    await client.execute({
      sql: `INSERT INTO impressions (event_id,user_id,surface,score_at_show,shown_at) VALUES (?,?,?,?,?)`,
      args: [row.id, "owner", "tg_digest", row.score, nowIso],
    });
  }
}

async function recordFeedback(client: Client, eventId: string, userId: string, label: string, channel: string): Promise<void> {
  const dimension = LABEL_DIMENSION[label] ?? "intent";
  await client.execute({
    sql: `INSERT INTO feedback_events (event_id,user_id,dimension,label,channel,created_at) VALUES (?,?,?,?,?,?)`,
    args: [eventId, userId, dimension, label, channel, new Date().toISOString()],
  });
}

async function handleUpdate(env: Env, update: any): Promise<void> {
  const client = db(env);

  if (update.callback_query) {
    const cq = update.callback_query;
    const [, eventId, label] = String(cq.data).split("|");
    if (eventId && label) {
      await recordFeedback(client, eventId, String(cq.from.id), label, "tg_button");
    }
    await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: `Записал: ${label} ✅` });
    return;
  }

  const msg = update.message;
  if (!msg || !msg.text) return;
  const chatId = msg.chat.id;
  const text = String(msg.text).trim();

  if (text.startsWith("/start")) {
    const arg = text.split(/\s+/)[1] ?? "";
    if (arg.startsWith("fb_")) {
      const parts = arg.split("_"); // fb_<raid>_<label>
      if (parts.length === 3) {
        await recordFeedback(client, `ra:${parts[1]}`, String(msg.from.id), parts[2], "deeplink");
        await tg(env, "sendMessage", { chat_id: chatId, text: "Отметил ✅" });
        return;
      }
    }
    await tg(env, "sendMessage", { chat_id: chatId, text: "Event Radar. /digest — топ, /free, /calendar, /stats." });
  } else if (text.startsWith("/digest")) {
    await sendDigest(env, client, chatId);
  } else if (text.startsWith("/free")) {
    await sendDigest(env, client, chatId, "AND e.is_free = 1");
  } else if (text.startsWith("/stats")) {
    const ev = await client.execute("SELECT COUNT(*) AS n FROM events");
    const fb = await client.execute("SELECT COUNT(*) AS n FROM feedback_events");
    await tg(env, "sendMessage", { chat_id: chatId, text: `events: ${ev.rows[0].n}\nfeedback: ${fb.rows[0].n}` });
  } else if (text.startsWith("/calendar")) {
    const rs = await client.execute("SELECT value FROM app_state WHERE key='calendar_id'");
    const cal = (rs.rows[0] as any)?.value;
    await tg(env, "sendMessage", {
      chat_id: chatId,
      text: cal ? `https://calendar.google.com/calendar/embed?src=${cal}` : "Календарь ещё не создан.",
    });
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // One-shot: point Telegram's webhook at this Worker (call from a browser/curl).
    if (request.method === "GET" && url.pathname === `/setup/${env.WEBHOOK_SECRET}`) {
      const hookUrl = `${url.origin}/tg/${env.WEBHOOK_SECRET}`;
      const result = await tg(env, "setWebhook", { url: hookUrl, allowed_updates: ["message", "callback_query"] });
      return new Response(JSON.stringify({ hookUrl, telegram: result }, null, 2));
    }

    if (request.method !== "POST" || url.pathname !== `/tg/${env.WEBHOOK_SECRET}`) {
      return new Response("event-radar-bot ok");
    }
    try {
      await handleUpdate(env, await request.json());
    } catch (error) {
      console.log("update error", error);
    }
    // Always 200 so Telegram doesn't retry-storm.
    return new Response("ok");
  },

  async scheduled(_event: any, env: Env): Promise<void> {
    if (!env.TELEGRAM_CHAT_ID) return; // no group configured yet
    await sendDigest(env, db(env), env.TELEGRAM_CHAT_ID);
  },
};
