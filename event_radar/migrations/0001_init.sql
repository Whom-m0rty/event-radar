-- Initial schema. Mirrors spec section 3 verbatim.
-- Feedback tables are append-only and self-describing on purpose: the labelled
-- data must outlive every other part of the system (spec section 7).

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,        -- 'ra:' + ra_id
    ra_id           TEXT UNIQUE,
    title           TEXT,
    venue_name      TEXT,
    city            TEXT,
    starts_at       TEXT,
    ends_at         TEXT,
    price_min       REAL,
    is_free         INTEGER,
    url             TEXT,
    description_raw TEXT,
    lineup_raw      TEXT,                    -- json array of artist names
    is_open_air     INTEGER,
    first_seen_at   TEXT,
    last_seen_at    TEXT
);

CREATE TABLE IF NOT EXISTS affinity (
    artist_name_normalized TEXT PRIMARY KEY,
    weight                 REAL,
    source                 TEXT,             -- spotify_top | spotify_followed | lastfm_similar
    origin                 TEXT,             -- for lastfm_similar: who it descended from
    updated_at             TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    event_id    TEXT,
    score       REAL,
    breakdown   TEXT,                        -- json: contribution of each factor
    computed_at TEXT
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    id             INTEGER PRIMARY KEY,
    event_id       TEXT NOT NULL,
    features_json  TEXT NOT NULL,            -- full output of build_features()
    score          REAL,
    breakdown_json TEXT,
    profile_hash   TEXT,
    profile_json   TEXT,                     -- the whole profile; yes, duplicated, yes, on purpose
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id          INTEGER PRIMARY KEY,
    event_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,               -- tg_user_id or 'owner'
    dimension   TEXT NOT NULL,               -- 'intent' | 'experience'
    label       TEXT NOT NULL,
    channel     TEXT NOT NULL,               -- gcal_color | gcal_rsvp | tg_button | deeplink | cli
    created_at  TEXT NOT NULL,
    note        TEXT,
    snapshot_id INTEGER REFERENCES feature_snapshots(id)
);

CREATE TABLE IF NOT EXISTS impressions (
    id            INTEGER PRIMARY KEY,
    event_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    surface       TEXT NOT NULL,             -- calendar | tg_digest | tg_alert
    position      INTEGER,
    score_at_show REAL,
    shown_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync (
    event_id           TEXT PRIMARY KEY,
    gcal_event_id      TEXT,
    tg_message_id      TEXT,
    last_pushed_score  REAL,
    last_color_id      TEXT,
    last_rsvp_status   TEXT
);

CREATE INDEX IF NOT EXISTS idx_feedback_lookup
    ON feedback_events (event_id, user_id, dimension, created_at);
CREATE INDEX IF NOT EXISTS idx_impressions_shown_at
    ON impressions (shown_at);
CREATE INDEX IF NOT EXISTS idx_events_starts_at
    ON events (starts_at);
