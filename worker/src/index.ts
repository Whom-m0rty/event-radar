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

function line(row: any): string {
  const price = row.is_free ? "free" : "€?";
  const when = String(row.starts_at ?? "").slice(0, 16).replace("T", " ");
  return `*[${Math.round(Number(row.score))}]* ${row.title}\n${when} · ${row.venue_name} · ${price}`;
}

async function sendDigest(env: Env, client: Client, chatId: number | string, where = ""): Promise<void> {
  const threshold = Number(env.PUSH_THRESHOLD ?? "35");
  const size = Number(env.DIGEST_SIZE ?? "10");
  const sql =
    `SELECT e.id, e.title, e.venue_name, e.starts_at, e.is_free, s.score ` +
    `FROM events e JOIN scores s ON s.event_id = e.id ` +
    `WHERE s.score >= ? ${where} ORDER BY s.score DESC LIMIT ?`;
  const rs = await client.execute({ sql, args: [threshold, size] });
  if (rs.rows.length === 0) {
    await tg(env, "sendMessage", { chat_id: chatId, text: "Пока нечего показать." });
    return;
  }
  const now = new Date().toISOString();
  for (const row of rs.rows as any[]) {
    await tg(env, "sendMessage", {
      chat_id: chatId, text: line(row), parse_mode: "Markdown",
      reply_markup: intentKeyboard(String(row.id)),
    });
    await client.execute({
      sql: `INSERT INTO impressions (event_id,user_id,surface,score_at_show,shown_at) VALUES (?,?,?,?,?)`,
      args: [row.id, "owner", "tg_digest", row.score, now],
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
