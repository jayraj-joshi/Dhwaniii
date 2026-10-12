import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, storage

router = APIRouter(prefix="/api")

Name = Field(min_length=1, max_length=40)


async def current_profile(request: Request) -> asyncpg.Record:
    """The only place a profile is resolved (D10): X-Profile-Id header, else ?profile=. Phase 6 replaces this."""
    raw = request.headers.get("x-profile-id") or request.query_params.get("profile")
    row = None
    if raw and raw.isdigit() and len(raw) < 18:
        row = await request.app.state.pool.fetchrow("SELECT * FROM profiles WHERE id = $1", int(raw))
    if row is None:
        raise HTTPException(401, "Unknown or missing profile")
    return row


def me_json(r) -> dict:
    return {"id": r["id"], "name": r["name"], "avatar": r["avatar"], "prefs": r["prefs"]}


class ProfileIn(BaseModel):
    name: str = Name
    avatar: str = Field("", max_length=16)


class MePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    avatar: str | None = Field(None, max_length=16)
    prefs: dict | None = None


@router.get("/profiles")
async def list_profiles(request: Request):
    rows = await request.app.state.pool.fetch("SELECT id, name, avatar FROM profiles ORDER BY id")
    return [dict(r) for r in rows]


@router.post("/profiles", status_code=201)
async def create_profile(body: ProfileIn, request: Request):
    try:
        row = await request.app.state.pool.fetchrow(
            "INSERT INTO profiles (name, avatar) VALUES ($1, $2) RETURNING *", body.name.strip(), body.avatar)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "A profile with that name already exists")
    return me_json(row)


@router.get("/me")
async def get_me(p=Depends(current_profile)):
    return me_json(p)


@router.patch("/me")
async def patch_me(body: MePatch, request: Request, p=Depends(current_profile)):
    try:
        row = await request.app.state.pool.fetchrow(
            "UPDATE profiles SET name = coalesce($2, name), avatar = coalesce($3, avatar), "
            "prefs = prefs || coalesce($4::jsonb, '{}'::jsonb) WHERE id = $1 RETURNING *",
            p["id"], body.name and body.name.strip(), body.avatar, body.prefs)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "A profile with that name already exists")
    return me_json(row)


@router.get("/settings")
async def settings(request: Request, p=Depends(current_profile)):
    """Server-wide and read-only (D37): the codec is chosen in .env, the storage budget is STORAGE_LIMIT_GB."""
    pool = request.app.state.pool
    return {"codec": config.PREF_CODEC, "storage_limit_bytes": int(config.STORAGE_LIMIT_GB * storage.GB),
            "storage_used_bytes": int(await storage.storage_used(pool)), "file_count": await pool.fetchval("SELECT count(*) FROM files")}
