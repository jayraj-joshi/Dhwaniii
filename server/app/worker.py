"""arq worker: download_track (§9.5), feed_backlog (D27), evict (D33)."""
import asyncio
import logging
import os
import random
import time

import yt_dlp
from arq import Retry, cron, func
from arq.connections import RedisSettings

from . import admin, autoplay, config, db, enrich, importer, storage, youtube
from .text import parse_title
from .tracks import apply_music_metadata, cache_state_for, enqueue_download, ensure_track, refresh_track_search
from .ws import publish_all

log = logging.getLogger("resonance.worker")
POLITE_SLEEP = (1.0, 3.0)  # seconds between jobs, to be gentle with YouTube (D28)


async def startup(ctx):
    ctx["pool"] = await db.create_pool()
    await db.migrate(ctx["pool"])


async def shutdown(ctx):
    await ctx["pool"].close()


def _download(video_id: str, on_progress) -> dict:
    """Blocking yt-dlp run: audio remuxed to PREF_CODEC at the D16 path, plus the thumbnail next to it."""
    out_dir = storage.abs_path(storage.audio_rel(video_id, "x")).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    last = [0.0]

    def hook(d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d["status"] != "downloading" or not total or time.monotonic() - last[0] < 0.5:  # at most 2 events/s
            return
        last[0] = time.monotonic()
        on_progress(int(d["downloaded_bytes"] * 100 / total))

    opts = youtube.ytdlp_opts(
        outtmpl=f"{out_dir}/{video_id}.%(ext)s", writethumbnail=True, noprogress=True, progress_hooks=[hook],
        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": config.PREF_CODEC, "preferredquality": "192"}],
        retries=5, fragment_retries=5, concurrent_fragment_downloads=4)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)


def _is_permanent(msg: str) -> bool:
    """D28: retrying can't help. A country block is only final when there is no cookies file to try."""
    if "not available in your country" in msg.lower() and os.path.exists(config.YTDLP_COOKIES_FILE):
        return False
    return bool(youtube.UNAVAILABLE_RE.search(msg))


async def _fail(ctx, video_id: str, err: Exception, attempt: int):
    pool, redis, msg = ctx["pool"], ctx["redis"], str(err)[:500]
    if not _is_permanent(msg) and attempt < config.DOWNLOAD_RETRIES + 1:
        await pool.execute("UPDATE download_jobs SET status = 'queued', error = $2, updated_at = now() WHERE video_id = $1", video_id, msg)
        raise Retry(defer=30 * 4 ** (attempt - 1))  # 30 s, 2 min, 8 min
    log.warning("download failed for good (%s): %s", video_id, msg)
    await pool.execute("UPDATE download_jobs SET status = 'failed', error = $2, updated_at = now() WHERE video_id = $1", video_id, msg)
    await publish_all(redis, {"type": "download_failed", "video_id": video_id, "error": msg})
    await redis.delete(f"dl:lock:{video_id}")


async def _finish(ctx, video_id: str, info: dict) -> int:
    """Steps 4-8 of §9.5. Returns the track id."""
    pool = ctx["pool"]
    raw_title = info.get("title") or video_id
    parsed = parse_title(info)
    duration_ms = int(info["duration"] * 1000) if info.get("duration") else None
    channel = info.get("channel") or info.get("uploader")
    row = await pool.fetchrow("SELECT id, track_id FROM sources WHERE video_id = $1", video_id)
    if row is None:  # played without ever being ensured: create the track from what the download told us
        await ensure_track(pool, {"video_id": video_id, "title": parsed["title"], "artists": [{"name": n} for n in parsed["artists"]],
                                  "album": {"title": parsed["album"]} if parsed["album"] else None, "duration_ms": duration_ms, "channel": channel})
        row = await pool.fetchrow("SELECT id, track_id FROM sources WHERE video_id = $1", video_id)
    elif parsed["stage"] in ("fields", "topic"):  # music metadata beats a search hint; a rule-based guess does not
        await apply_music_metadata(pool, row["track_id"], parsed, duration_ms)
    source_id, track_id = row["id"], row["track_id"]
    await pool.execute("UPDATE sources SET duration_ms = coalesce(duration_ms, $2), channel = coalesce(channel, $3), "
                       "title_raw = coalesce(title_raw, $4) WHERE id = $1", source_id, duration_ms, channel, raw_title)
    await pool.execute("UPDATE tracks SET duration_ms = coalesce(duration_ms, $2) WHERE id = $1", track_id, duration_ms)
    if parsed["year"]:
        await pool.execute("UPDATE albums SET year = $2 WHERE year IS NULL AND id = (SELECT album_id FROM tracks WHERE id = $1)", track_id, parsed["year"])

    # artwork: the original thumbnail becomes art/track/<video_id>.jpg at 600x600, then is deleted
    codec = config.PREF_CODEC
    audio = storage.abs_path(storage.audio_rel(video_id, codec))
    if not audio.exists():
        raise FileNotFoundError(f"yt-dlp produced no {codec} file for {video_id}")
    thumbs = [p for p in audio.parent.glob(f"{video_id}.*") if p != audio]
    art_rel = storage.art_rel("track", video_id)
    if thumbs:
        try:
            await asyncio.to_thread(storage.crop_art, thumbs[0], storage.abs_path(art_rel))
            await pool.execute("UPDATE tracks SET artwork_path = $2 WHERE id = $1", track_id, art_rel)
        except Exception as e:
            log.warning("artwork failed for %s: %s", video_id, e)
        for p in thumbs:
            p.unlink(missing_ok=True)

    await refresh_track_search(pool, [track_id])
    try:
        await enrich.enrich_track(pool, track_id)  # clean YouTube Music metadata and the album cover; never fails the download
    except Exception:
        log.exception("enrichment failed for %s", video_id)
    lufs, gain = await asyncio.to_thread(storage.measure_loudness, audio)
    await enrich.write_file_tags(pool, track_id, audio)
    bitrate = await asyncio.to_thread(storage.probe_bitrate_kbps, audio)  # bytes are read after tagging, so SUM(files.bytes) is exact
    await pool.execute(
        "INSERT INTO files (source_id, path, codec, bitrate, bytes, loudness_lufs, gain_db) VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (source_id) DO UPDATE SET path = $2, codec = $3, bitrate = $4, bytes = $5, loudness_lufs = $6, gain_db = $7",
        source_id, storage.audio_rel(video_id, codec), codec, bitrate, audio.stat().st_size, lufs, gain)
    # a track keeps one file: when a different source was chosen (M2.3-B), the file of the old one goes
    for r in await pool.fetch("DELETE FROM files f USING sources s WHERE s.id = f.source_id AND s.track_id = $1 AND f.source_id <> $2 RETURNING f.path", track_id, source_id):
        storage.abs_path(r["path"]).unlink(missing_ok=True)
    return track_id


async def download_track(ctx, video_id: str):
    pool, redis = ctx["pool"], ctx["redis"]
    if await cache_state_for(pool, video_id) == "cached":  # step 1: idempotent
        await pool.execute("UPDATE download_jobs SET status = 'done', updated_at = now() WHERE video_id = $1", video_id)
        await redis.delete(f"dl:lock:{video_id}")
        return
    await pool.execute(
        "INSERT INTO download_jobs (video_id, status, priority, attempts) VALUES ($1, 'running', 'high', 1) "
        "ON CONFLICT (video_id) DO UPDATE SET status = 'running', attempts = download_jobs.attempts + 1, updated_at = now()", video_id)
    loop = asyncio.get_running_loop()
    progress = lambda pct: asyncio.run_coroutine_threadsafe(
        publish_all(redis, {"type": "download_progress", "video_id": video_id, "percent": pct}), loop)
    try:
        info = await asyncio.to_thread(_download, video_id, progress)
        track_id = await _finish(ctx, video_id, info)
    except Exception as e:
        return await _fail(ctx, video_id, e, ctx.get("job_try", 1))
    await pool.execute("UPDATE download_jobs SET status = 'done', error = NULL, updated_at = now() WHERE video_id = $1", video_id)
    await publish_all(redis, {"type": "track_cached", "video_id": video_id, "track_id": track_id})
    await redis.delete(f"dl:lock:{video_id}")
    await storage.evict(pool)
    await asyncio.sleep(random.uniform(*POLITE_SLEEP))


async def feed_backlog(ctx):
    """Move the oldest low-priority backlog rows into arq while the queue is short (D27 step 4)."""
    free = config.WORKER_MAX_JOBS - await ctx["redis"].zcard("arq:queue")
    if free <= 0:
        return
    rows = await ctx["pool"].fetch(
        "SELECT video_id FROM download_jobs WHERE status = 'queued' AND priority = 'low' ORDER BY created_at LIMIT $1", free * 4)
    for r in rows:  # rows already in arq hold the lock, so enqueue_download skips them
        if free > 0 and await enqueue_download(ctx["redis"], ctx["redis"], r["video_id"]):
            free -= 1


async def update_ytdlp(ctx):
    """Admin button (M4.4-B): update yt-dlp in this container's virtualenv, then restart onto it."""
    res = await admin.update_here()
    admin.exit_soon()
    return res


async def evict(ctx):
    return await storage.evict(ctx["pool"])


class Settings:
    functions = [download_track, update_ytdlp, func(importer.run_import, name="run_import", timeout=3600, max_tries=1),  # a retry would add every track twice
                 func(autoplay.autoplay_extend, name="autoplay_extend", timeout=150, max_tries=1)]  # a retry would spend Tavily/LLM credits again
    cron_jobs = [cron(feed_backlog, second={0, 30}), cron(evict, minute=0, second=0)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(config.REDIS_URL)
    max_jobs = config.WORKER_MAX_JOBS
    max_tries = config.DOWNLOAD_RETRIES + 1
    keep_result = 0
    job_timeout = 300
