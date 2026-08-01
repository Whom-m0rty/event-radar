-- OAuth token storage (spec 4.1: refresh automatically, keep in the DB).
CREATE TABLE IF NOT EXISTS oauth_tokens (
    provider      TEXT PRIMARY KEY,     -- 'spotify'
    access_token  TEXT,
    refresh_token TEXT,
    expires_at    TEXT,                 -- ISO8601 UTC
    scope         TEXT,
    updated_at    TEXT
);
