-- Seed tracks imported from the Spotify web player (Liked Songs / a playlist).
-- Used when the Web API is unavailable (Free account): the taste profile is
-- built from these instead of /me/top. Deduped by track_id so re-pasting
-- overlapping scroll chunks never double-counts an artist.
CREATE TABLE IF NOT EXISTS seed_tracks (
    track_id     TEXT PRIMARY KEY,
    title        TEXT,
    artists_json TEXT NOT NULL,     -- json array of raw artist names
    source       TEXT,              -- 'spotify_liked'
    imported_at  TEXT
);
