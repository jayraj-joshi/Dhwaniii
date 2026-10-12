"""GET /api/stream/{video_id}: disk first (D29), live proxy on a miss (D30). POST /api/tracks/{video_id}/cache."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from .. import storage, youtube
from ..tracks import request_download
from .profiles import current_profile

router = APIRouter(prefix="/api")
VideoId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{11}$")]  # D12
FILE_MIME = {"m4a": "audio/mp4", "mp3": "audio/mpeg"}
PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


async def _live(request: Request, video_id: str) -> StreamingResponse:
    s = request.app.state
    rng = request.headers.get("range", "bytes=0-")
    resp = None
    for attempt in (1, 2):
        try:
            info = await youtube.resolve_audio_url(s.redis, video_id, fresh=attempt == 2)
        except youtube.Unavailable as e:
            raise HTTPException(404, f"Video unavailable: {e}")
        except Exception as e:
            raise HTTPException(502, f"Could not resolve audio: {e}")
        resp = await s.http.send(s.http.build_request("GET", info["url"], headers={**info["http_headers"], "Range": rng}), stream=True)
        if resp.status_code in (403, 410) and attempt == 1:  # the signed URL expired: re-resolve once
            await resp.aclose()
            await s.redis.delete(f"su:{video_id}")
            continue
        break
    if resp.status_code >= 400 and resp.status_code != 416:
        await resp.aclose()
        raise HTTPException(502, f"Upstream returned {resp.status_code}")

    async def body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    status, headers = resp.status_code, {h: resp.headers[h] for h in PASS_HEADERS if h in resp.headers}
    if "range" not in request.headers and status == 206:  # we asked upstream for bytes=0-; the client didn't ask for a range
        status = 200
        headers.pop("content-range", None)
    return StreamingResponse(body(), status_code=status, headers=headers)


def _attachment_name(title: str, artists: str, codec: str) -> str:
    name = f"{artists} - {title}" if artists else title
    name = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "track"
    return f"{name}.{codec or 'm4a'}"


@router.get("/stream/{video_id}")
async def stream(video_id: VideoId, request: Request, download: bool = Query(False), p=Depends(current_profile)):
    s = request.app.state
    row = await s.pool.fetchrow(
        """SELECT f.source_id, f.path, f.codec, t.title,
                  (SELECT string_agg(a.name, ', ' ORDER BY ta.position)
                     FROM track_artists ta JOIN artists a ON a.id = ta.artist_id WHERE ta.track_id = t.id) AS artists
           FROM sources s JOIN files f ON f.source_id = s.id JOIN tracks t ON t.id = s.track_id WHERE s.video_id = $1""",
        video_id)
    if row and (path := storage.resolve(row["path"])):
        rng = request.headers.get("range")
        if not rng or rng.replace(" ", "").startswith("bytes=0-"):  # count a play once, not once per chunk
            await s.pool.execute("UPDATE files SET last_played = $2 WHERE source_id = $1", row["source_id"], datetime.now(timezone.utc))
        headers = {"Content-Disposition": f'attachment; filename="{_attachment_name(row["title"], row["artists"], row["codec"])}"'} if download else None
        return FileResponse(path, media_type=FILE_MIME.get(row["codec"], "audio/mp4"), headers=headers)
    if download:
        raise HTTPException(409, "Not downloaded to the server yet. Try again in a moment.")
    await request_download(s.pool, s.redis, s.arq, video_id, "high")  # play it live, and keep a copy for next time
    return await _live(request, video_id)


@router.post("/tracks/{video_id}/cache")
async def cache(video_id: VideoId, request: Request, priority: str = Query("high", pattern="^(high|low)$"), p=Depends(current_profile)):
    s = request.app.state
    return {"cache_state": await request_download(s.pool, s.redis, s.arq, video_id, priority)}
