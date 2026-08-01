-- Small key/value store for app state (e.g. the created calendar id).
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
