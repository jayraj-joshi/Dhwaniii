"""Admin API (M4.4-B, D44: like everything else, open to whoever can reach the port)."""
import yt_dlp
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import admin, config, storage
from ..tracks import attach_source, request_download
from .profiles import current_profile
from .tracks import ranked_candidates, track_detail

router = APIRouter(prefix="/api/admin")


@router.get("/status")
async def status(request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    counts = {s: 0 for s in ("queued", "running", "done", "failed")}
    by_priority = {"high": 0, "low": 0}
    for r in await pool.fetch("SELECT status, priority, count(*) AS n FROM download_jobs GROUP BY 1, 2"):
        counts[r["status"]] += r["n"]
        if r["status"] in ("queued", "running"):
            by_priority[r["priority"]] += r["n"]
    failed = await pool.fetch(
        "SELECT j.video_id, j.error, j.attempts, j.updated_at, t.id AS track_id, t.title, "
        "(SELECT a.name FROM artists a WHERE a.id = t.primary_artist_id) AS artist "
        "FROM download_jobs j LEFT JOIN sources s ON s.video_id = j.video_id LEFT JOIN tracks t ON t.id = s.track_id "
        "WHERE j.status = 'failed' ORDER BY j.updated_at DESC LIMIT 100")
    used = await storage.storage_used(pool)
    limit = int(config.STORAGE_LIMIT_GB * storage.GB)
    pinned = await pool.fetchval(
        "SELECT coalesce(sum(f.bytes), 0) FROM files f JOIN sources s ON s.id = f.source_id WHERE EXISTS (SELECT 1 FROM likes l WHERE l.track_id = s.track_id) "
        "OR EXISTS (SELECT 1 FROM playlist_tracks pt JOIN device_downloads dd ON dd.playlist_id = pt.playlist_id WHERE pt.track_id = s.track_id)")
    return {"jobs": counts, "active_by_priority": by_priority,
            "failed": [{"video_id": r["video_id"], "error": r["error"], "attempts": r["attempts"], "updated_at": r["updated_at"],
                        "track": {"id": r["track_id"], "title": r["title"], "artist": r["artist"]} if r["track_id"] else None} for r in failed],
            "disk": {"used_bytes": int(used), "limit_bytes": limit, "pinned_bytes": int(pinned), "over_budget": used > limit,
                     "file_count": await pool.fetchval("SELECT count(*) FROM files")},
            "yt_dlp_version": yt_dlp.version.__version__}


@router.post("/jobs/{video_id}/retry")
async def retry(video_id: str, request: Request, p=Depends(current_profile)):
    s = request.app.state
    if not await s.pool.fetchval("SELECT 1 FROM download_jobs WHERE video_id = $1", video_id):
        raise HTTPException(404, "No such job")
    return {"cache_state": await request_download(s.pool, s.redis, s.arq, video_id, "high")}  # a high request resets attempts and the error


@router.post("/jobs/{video_id}/alternative")
async def alternative(video_id: str, request: Request, p=Depends(current_profile)):
    """Give up on this upload: take the next-best one from a search for the track, using its existing metadata."""
    s = request.app.state
    track_id = await s.pool.fetchval("SELECT track_id FROM sources WHERE video_id = $1", video_id)
    if track_id is None:
        raise HTTPException(404, "No track for that video")
    own = {r["video_id"] for r in await s.pool.fetch("SELECT video_id FROM sources WHERE track_id = $1", track_id)}
    best = next((c for c in await ranked_candidates(s, track_id) if c["video_id"] not in own and not c["in_library"]), None)
    if best is None:
        raise HTTPException(409, "No other upload found for this track")
    await attach_source(s.pool, s.redis, s.arq, track_id, best["video_id"], best["channel"], best["title"], best["duration_ms"])
    return await track_detail(s.pool, p["id"], track_id)


@router.post("/ytdlp/update")
async def update_ytdlp(request: Request, p=Depends(current_profile)):
    """Update yt-dlp in the worker and here, then restart both. The response arrives before this process exits."""
    s = request.app.state
    await s.arq.enqueue_job("update_ytdlp", _job_id="update_ytdlp")  # the worker updates its own copy and restarts
    try:
        res = await admin.update_here()
    except RuntimeError as e:
        raise HTTPException(502, f"Could not update yt-dlp: {e}")
    admin.exit_soon()
    return {**res, "restarting": True}
