"""Playlists (D18, D19, D45). Owned by one profile until collaborators arrive in Phase 5."""
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Literal

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.responses import Response

from .. import storage
from ..text import normalize
from ..tracks import get_tracks, request_download, request_downloads_for_tracks
from .profiles import current_profile

router = APIRouter(prefix="/api")
MAX_COVER = 5 * 1024 * 1024

PLAYLIST_SELECT = """
SELECT p.id, p.name, p.owner_id, p.description, p.cover_path, p.pinned_to_sidebar, p.created_at, p.updated_at,
    (SELECT count(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id) AS track_count,
    (SELECT coalesce(array_agg(x.track_id), '{}') FROM
        (SELECT track_id FROM playlist_tracks WHERE playlist_id = p.id ORDER BY position, id LIMIT 4) x) AS collage
FROM playlists p
"""

# sidebar/library order: pinned first, then most recently played (history context), then most recently edited
RECENT_ORDER = """
ORDER BY p.pinned_to_sidebar DESC,
    coalesce((SELECT max(h.played_at) FROM play_history h WHERE h.profile_id = $1 AND h.context = 'playlist:' || p.id), p.updated_at) DESC,
    p.id DESC
"""


def playlist_json(r) -> dict:
    """PlaylistSummary (§7.1) plus description and pin flag. `?v=` busts the 7-day art cache when the cover changes."""
    return {"id": r["id"], "name": r["name"], "owner_id": r["owner_id"], "description": r["description"],
            "pinned_to_sidebar": r["pinned_to_sidebar"], "track_count": r["track_count"],
            "cover_url": f"/api/art/playlist/{r['id']}?v={int(r['updated_at'].timestamp())}" if r["cover_path"] else None,
            "collage": [f"/api/art/track/{t}" for t in r["collage"]], "created_at": r["created_at"], "updated_at": r["updated_at"]}


async def owned(db, playlist_id: int, profile_id: int, lock: bool = False):
    row = await db.fetchrow(PLAYLIST_SELECT + " WHERE p.id = $1 AND p.owner_id = $2" + (" FOR UPDATE OF p" if lock else ""), playlist_id, profile_id)
    if row is None:
        raise HTTPException(404, "No such playlist")
    return row


async def item_json(pool, profile_id: int, rows) -> list[dict]:
    tracks = await get_tracks(pool, profile_id, [r["track_id"] for r in rows])
    return [{"item_id": r["id"], "position": float(r["position"]), "added_at": r["added_at"], "track": t} for r, t in zip(rows, tracks)]


class PlaylistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=500)


class PlaylistPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    pinned_to_sidebar: bool | None = None


class AddTracks(BaseModel):
    track_ids: list[int] = Field(min_length=1, max_length=500)
    at: Literal["end", "start"] = "end"


class RemoveItems(BaseModel):
    item_ids: list[int] = Field(max_length=500)


class MoveItem(BaseModel):
    item_id: int
    before_item_id: int | None = None  # None = move to the end


@router.get("/playlists")
async def list_playlists(request: Request, p=Depends(current_profile)):
    rows = await request.app.state.pool.fetch(PLAYLIST_SELECT + " WHERE p.owner_id = $1 " + RECENT_ORDER, p["id"])
    return [playlist_json(r) for r in rows]


@router.post("/playlists", status_code=201)
async def create_playlist(body: PlaylistIn, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    pid = await pool.fetchval("INSERT INTO playlists (owner_id, name, name_norm, description) VALUES ($1, $2, $3, $4) RETURNING id",
                              p["id"], body.name.strip(), normalize(body.name), body.description)
    return playlist_json(await owned(pool, pid, p["id"]))


@router.get("/playlists/{playlist_id}")
async def get_playlist(playlist_id: int, request: Request, p=Depends(current_profile)):
    s = request.app.state
    pl = await owned(s.pool, playlist_id, p["id"])
    rows = await s.pool.fetch("SELECT id, track_id, position, added_at FROM playlist_tracks WHERE playlist_id = $1 ORDER BY position, id", playlist_id)
    items = await item_json(s.pool, p["id"], rows)
    for it in items[:5]:  # opening a playlist warms its first five tracks at low priority (D19)
        if it["track"]["cache_state"] == "remote":
            await request_download(s.pool, s.redis, s.arq, it["track"]["video_id"], "low")
    offline = [r["device_id"] for r in await s.pool.fetch("SELECT device_id FROM device_downloads WHERE playlist_id = $1 ORDER BY created_at", playlist_id)]
    return {**playlist_json(pl), "items": items, "offline_devices": offline}  # devices keeping this playlist offline (M4.2-B)


@router.patch("/playlists/{playlist_id}")
async def patch_playlist(playlist_id: int, body: PlaylistPatch, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    await owned(pool, playlist_id, p["id"])
    name = body.name.strip() if body.name else None
    await pool.execute(
        "UPDATE playlists SET name = coalesce($2, name), name_norm = coalesce($3, name_norm), description = coalesce($4, description), "
        "pinned_to_sidebar = coalesce($5, pinned_to_sidebar), updated_at = now() WHERE id = $1",
        playlist_id, name, normalize(name) if name else None, body.description, body.pinned_to_sidebar)
    return playlist_json(await owned(pool, playlist_id, p["id"]))


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: int, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    pl = await owned(pool, playlist_id, p["id"])
    await pool.execute("DELETE FROM playlists WHERE id = $1", playlist_id)  # tracks and files are kept
    if pl["cover_path"]:
        storage.abs_path(pl["cover_path"]).unlink(missing_ok=True)
    return Response(status_code=204)


# --- cover (D45): one 600x600 JPEG, made by ffmpeg ---------------------------------------

def _image_suffix(b: bytes) -> str | None:
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return ".webp"
    if b[:4] == b"GIF8":
        return ".gif"
    return None


@router.put("/playlists/{playlist_id}/cover")
async def put_cover(playlist_id: int, file: UploadFile, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    await owned(pool, playlist_id, p["id"])
    data = await file.read(MAX_COVER + 1)
    if len(data) > MAX_COVER:
        raise HTTPException(413, "Cover must be 5 MB or smaller")
    suffix = _image_suffix(data)
    if suffix is None:
        raise HTTPException(422, "Cover must be a JPEG, PNG, WebP or GIF image")
    rel = storage.art_rel("playlist", playlist_id)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / f"upload{suffix}"
        src.write_bytes(data)
        try:
            await asyncio.to_thread(storage.crop_art, src, storage.abs_path(rel))
        except subprocess.CalledProcessError:
            raise HTTPException(422, "Could not read that image")
    await pool.execute("UPDATE playlists SET cover_path = $2, updated_at = now() WHERE id = $1", playlist_id, rel)
    return playlist_json(await owned(pool, playlist_id, p["id"]))


@router.delete("/playlists/{playlist_id}/cover", status_code=204)
async def delete_cover(playlist_id: int, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    pl = await owned(pool, playlist_id, p["id"])
    if pl["cover_path"]:
        storage.abs_path(pl["cover_path"]).unlink(missing_ok=True)
        await pool.execute("UPDATE playlists SET cover_path = NULL, updated_at = now() WHERE id = $1", playlist_id)
    return Response(status_code=204)


# --- tracks in a playlist (D18: numeric positions, nothing is ever renumbered) -------------

@router.post("/playlists/{playlist_id}/tracks", status_code=201)
async def add_tracks(playlist_id: int, body: AddTracks, request: Request, p=Depends(current_profile)):
    s = request.app.state
    async with s.pool.acquire() as c, c.transaction():
        await owned(c, playlist_id, p["id"], lock=True)  # serializes position math per playlist
        known = {r["id"] for r in await c.fetch("SELECT id FROM tracks WHERE id = ANY($1::bigint[])", list(set(body.track_ids)))}
        if missing := sorted(set(body.track_ids) - known):
            raise HTTPException(422, f"Unknown track ids: {missing}")
        n = len(body.track_ids)
        if body.at == "end":
            first = (await c.fetchval("SELECT coalesce(max(position), 0) FROM playlist_tracks WHERE playlist_id = $1", playlist_id)) + 1
        else:  # insert before everything, keeping the given order
            first = (await c.fetchval("SELECT coalesce(min(position), 1) FROM playlist_tracks WHERE playlist_id = $1", playlist_id)) - n
        rows = [await c.fetchrow("INSERT INTO playlist_tracks (playlist_id, track_id, position, added_by) VALUES ($1, $2, $3, $4) "
                                 "RETURNING id, track_id, position, added_at", playlist_id, tid, first + i, p["id"])
                for i, tid in enumerate(body.track_ids)]
        await c.execute("UPDATE playlists SET updated_at = now() WHERE id = $1", playlist_id)
    await request_downloads_for_tracks(s.pool, s.redis, s.arq, body.track_ids, "low")
    return await item_json(s.pool, p["id"], rows)


@router.delete("/playlists/{playlist_id}/tracks", status_code=204)
async def remove_tracks(playlist_id: int, body: RemoveItems, request: Request, p=Depends(current_profile)):
    pool = request.app.state.pool
    await owned(pool, playlist_id, p["id"])
    await pool.execute("DELETE FROM playlist_tracks WHERE playlist_id = $1 AND id = ANY($2::bigint[])", playlist_id, body.item_ids)
    await pool.execute("UPDATE playlists SET updated_at = now() WHERE id = $1", playlist_id)
    return Response(status_code=204)


@router.patch("/playlists/{playlist_id}/tracks")
async def move_track(playlist_id: int, body: MoveItem, request: Request, p=Depends(current_profile)):
    async with request.app.state.pool.acquire() as c, c.transaction():
        await owned(c, playlist_id, p["id"], lock=True)
        pos = {r["id"]: r["position"] for r in await c.fetch("SELECT id, position FROM playlist_tracks WHERE playlist_id = $1", playlist_id)}
        if body.item_id not in pos:
            raise HTTPException(404, "No such item in this playlist")
        if body.before_item_id is None:
            new = max(pos.values()) + 1
        elif body.before_item_id == body.item_id:
            return {"item_id": body.item_id, "position": float(pos[body.item_id])}
        elif body.before_item_id not in pos:
            raise HTTPException(422, "before_item_id is not in this playlist")
        else:
            b = pos[body.before_item_id]
            a = max((v for k, v in pos.items() if v < b and k != body.item_id), default=None)
            new = b - 1 if a is None else (a + b) / 2
        await c.execute("UPDATE playlist_tracks SET position = $2 WHERE id = $1", body.item_id, new)
        await c.execute("UPDATE playlists SET updated_at = now() WHERE id = $1", playlist_id)
    return {"item_id": body.item_id, "position": float(new)}
