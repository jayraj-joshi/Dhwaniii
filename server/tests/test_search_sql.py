"""Search calibration (§9.3): every fixture query must hit/miss and rank as expected."""
import asyncio
import json
from pathlib import Path

import asyncpg
import pytest

from app import config
from app.text import normalize
from app.tracks import refresh_track_search

FIXTURE = json.loads((Path(__file__).parent / "search_fixture.json").read_text())


async def _load(url: str) -> dict[str, int]:
    c = await asyncpg.connect(url)
    ids: dict[str, int] = {}
    for n, t in enumerate(FIXTURE["tracks"]):
        art_ids = []
        for name in t["artists"]:
            art_ids.append(await c.fetchval(
                "INSERT INTO artists (name, name_norm) VALUES ($1, $2) "
                "ON CONFLICT (name_norm) WHERE ytm_channel_id IS NULL DO UPDATE SET name = artists.name RETURNING id",
                name, normalize(name)))
        alb = await c.fetchval(
            "INSERT INTO albums (title, title_norm, artist_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (artist_id, title_norm) WHERE ytm_browse_id IS NULL DO UPDATE SET title = albums.title RETURNING id",
            t["album"], normalize(t["album"]), art_ids[0])
        tid = await c.fetchval(
            "INSERT INTO tracks (title, title_norm, primary_artist_id, album_id) VALUES ($1, $2, $3, $4) RETURNING id",
            t["title"], normalize(t["title"]), art_ids[0], alb)
        for pos, a in enumerate(art_ids):
            await c.execute("INSERT INTO track_artists VALUES ($1, $2, $3, $4)", tid, a, "primary" if pos == 0 else "featured", pos)
        await c.execute("INSERT INTO sources (track_id, video_id) VALUES ($1, $2)", tid, f"fx{n:09d}")
        await refresh_track_search(c, [tid])
        ids[t["key"]] = tid
    await c.close()
    return ids


@pytest.fixture(scope="module")
def library(client):
    from tests.conftest import _url, TEST_DB
    async def clean():  # other test files leave tracks behind; the calibration needs a library of exactly the fixture
        c = await asyncpg.connect(_url(TEST_DB))
        await c.execute("TRUNCATE tracks, artists, albums CASCADE")
        await c.close()
    asyncio.run(clean())
    ids = asyncio.run(_load(_url(TEST_DB)))
    pid = client.post("/api/profiles", json={"name": "search-fixture"}).json()["id"]
    async def plays():
        c = await asyncpg.connect(_url(TEST_DB))
        for t in FIXTURE["tracks"]:
            if t["plays"]:
                await c.execute("INSERT INTO user_track_stats (profile_id, track_id, play_count) VALUES ($1, $2, $3)", pid, ids[t["key"]], t["plays"])
        await c.close()
    asyncio.run(plays())
    return ids, {"X-Profile-Id": str(pid)}


@pytest.mark.parametrize("case", FIXTURE["queries"], ids=[q["q"] for q in FIXTURE["queries"]])
def test_fixture_query(client, library, case):
    ids, h = library
    r = client.get("/api/search", params={"q": case["q"], "type": "songs"}, headers=h).json()
    assert r["cache_hit"] == (case["expect"] == "hit"), (r["songs"][:2], r["query_norm"])
    if "rank1" in case:
        assert r["songs"][0]["id"] == ids[case["rank1"]]


def test_top_is_exact_artist_and_suggest(client, library):
    ids, h = library
    top = client.get("/api/search", params={"q": "Adele"}, headers=h).json()["top"]
    assert top["kind"] == "artist" and top["item"]["name"] == "Adele"
    assert client.get("/api/search", params={"q": "tum hi ho"}, headers=h).json()["top"]["kind"] == "song"
    assert client.get("/api/suggest", params={"q": "a"}, headers=h).json() == []
    labels = [s["label"] for s in client.get("/api/suggest", params={"q": "tum"}, headers=h).json()]
    assert "Tum Hi Ho" in labels and len(labels) <= 8
