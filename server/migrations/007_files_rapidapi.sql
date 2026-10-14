-- 007_files_rapidapi: the file came from the RapidAPI fallback (no music fields, YouTube's video thumbnail at best).
-- The worker's redownload_rapidapi cron replaces it with yt-dlp's once YouTube lets us download again.
ALTER TABLE files ADD COLUMN rapidapi boolean NOT NULL DEFAULT false;
