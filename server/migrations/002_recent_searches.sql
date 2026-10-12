-- 002_recent_searches (M2.5-B). One row per profile and normalized query; `query` keeps the text as last typed.
CREATE TABLE recent_searches (
    profile_id  bigint NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    query_norm  text NOT NULL,
    query       text NOT NULL,
    searched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, query_norm)
);
CREATE INDEX recent_searches_newest ON recent_searches (profile_id, searched_at DESC);
