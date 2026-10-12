-- 004_imports (M3.4-B). `payload` holds the parsed CSV rows for the worker; it is never returned by the API.
CREATE TABLE imports (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id  bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('youtube', 'csv')),
    source_ref  text NOT NULL,
    playlist_id bigint REFERENCES playlists(id) ON DELETE SET NULL,
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    total       int NOT NULL DEFAULT 0,
    matched     int NOT NULL DEFAULT 0,
    unmatched   jsonb NOT NULL DEFAULT '[]',
    error       text,
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX imports_profile ON imports (profile_id, created_at DESC);
