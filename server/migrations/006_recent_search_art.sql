-- 006_recent_search_art: the song played from a recent search, so the list can show its artwork. Null for older rows.
ALTER TABLE recent_searches ADD COLUMN video_id text;
