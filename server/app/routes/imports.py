"""POST /api/import and GET /api/imports/{id} (M3.4-B)."""
import json
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .. import importer
from ..text import normalize
from .profiles import current_profile

router = APIRouter(prefix="/api")
MAX_CSV = 2 * 1024 * 1024
DEFAULT_NAME = "Imported playlist"


class ImportIn(BaseModel):
    url: str = Field(max_length=500)
    name: str | None = Field(None, min_length=1, max_length=100)


@router.post("/import", status_code=202)
async def create_import(request: Request, p=Depends(current_profile)):
    """JSON `{url, name?}` for a YouTube / YouTube Music playlist, or a multipart CSV upload (field `file`, optional `name`).
    The playlist exists straight away; `run_import` fills it. Progress arrives as `import_progress` over the WebSocket."""
    s = request.app.state
    payload = None
    if request.headers.get("content-type", "").startswith("multipart/"):
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(422, "Upload the CSV in a form field named 'file'")
        data = await f.read(MAX_CSV + 1)
        if len(data) > MAX_CSV:
            raise HTTPException(413, "The CSV must be 2 MB or smaller")
        try:
            payload = importer.parse_csv(data)
        except ValueError as e:
            raise HTTPException(422, str(e))
        kind, ref = "csv", (f.filename or "upload.csv")[:200]
        name = (form.get("name") or "").strip() or PurePath(ref).stem or DEFAULT_NAME
    else:
        try:
            body = ImportIn.model_validate(await request.json())
        except (ValueError, ValidationError):
            raise HTTPException(422, "Send JSON like {\"url\": \"https://music.youtube.com/playlist?list=...\"}")
        list_id = importer.playlist_id_from_url(body.url)
        if list_id is None:
            raise HTTPException(422, "That isn't a YouTube or YouTube Music playlist link (it needs ?list=...)")
        kind, ref, name = "youtube", list_id, (body.name or DEFAULT_NAME).strip()
    name = name[:100]
    async with s.pool.acquire() as c, c.transaction():
        pl = await c.fetchval("INSERT INTO playlists (owner_id, name, name_norm) VALUES ($1, $2, $3) RETURNING id", p["id"], name, normalize(name))
        imp = await c.fetchval("INSERT INTO imports (profile_id, kind, source_ref, playlist_id, total, payload) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                               p["id"], kind, ref, pl, len(payload) if payload else 0, payload)
    await s.arq.enqueue_job("run_import", imp, _job_id=f"import:{imp}")
    return {"import_id": imp, "playlist_id": pl}


@router.get("/imports/{import_id}")
async def get_import(import_id: int, request: Request, p=Depends(current_profile)):
    r = await request.app.state.pool.fetchrow(
        "SELECT status, total, matched, unmatched, playlist_id, error FROM imports WHERE id = $1 AND profile_id = $2", import_id, p["id"])
    if r is None:
        raise HTTPException(404, "No such import")
    return dict(r)
