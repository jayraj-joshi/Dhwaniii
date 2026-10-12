"""Small helpers shared by the API test files."""
import asyncio
import uuid

import asyncpg

from tests.conftest import TEST_DB, _url


def run(fn):
    """Run `fn(connection)` against the test DB (a bare connection: jsonb comes back as text)."""
    async def go():
        c = await asyncpg.connect(_url(TEST_DB))
        try:
            return await fn(c)
        finally:
            await c.close()
    return asyncio.run(go())


def prof(client, prefix="u"):
    return {"X-Profile-Id": str(client.post("/api/profiles", json={"name": f"{prefix}-{uuid.uuid4().hex[:8]}"}).json()["id"])}


def mk_track(client, h, title=None, duration_ms=200_000):
    """A provisional track (via ensure). Returns the Track object."""
    vid = "t" + uuid.uuid4().hex[:10]
    return client.post("/api/tracks/ensure", json=[{"video_id": vid, "title": title or f"Song {vid}", "artists": [{"name": f"Artist {vid}"}],
                                                      "duration_ms": duration_ms}], headers=h).json()[0]


def jobs(video_ids):
    async def q(c):
        return {r["video_id"]: (r["status"], r["priority"]) for r in await c.fetch("SELECT video_id, status, priority FROM download_jobs WHERE video_id = ANY($1)", list(video_ids))}
    return run(q)
