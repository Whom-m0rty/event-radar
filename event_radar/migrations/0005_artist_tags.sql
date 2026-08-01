-- Cached Last.fm genre tags per artist (for genre-overlap scoring).
CREATE TABLE IF NOT EXISTS artist_tags (
    artist_name_normalized TEXT PRIMARY KEY,
    tags_json  TEXT,          -- json object {tag: weight}
    updated_at TEXT
);
