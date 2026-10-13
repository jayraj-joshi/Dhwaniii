import uuid

import asyncpg
import asyncio

from tests.conftest import FAKE_REMOTE, TEST_DB, _url


def cand(vid, title, score=3.0):
    return {"video_id": vid, "title": title, "artists": [{"name": "Nobody", "ytm_channel_id": None}], "album": None,
            "duration_ms": 200_000, "thumb_url": None, "channel": None, "quality_score": score}


def profile(client):
    return {"X-Profile-Id": str(client.post("/api/profiles", json={"name": f"r-{uuid.uuid4().hex[:8]}"}).json()["id"])}


async def _add_source(vid):
    c = await asyncpg.connect(_url(TEST_DB))
    aid = await c.fetchval("INSERT INTO artists (name, name_norm) VALUES ('Zed', $1) RETURNING id", f"zed{vid}")
    tid = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id) VALUES ('Zork', 'zork', $1) RETURNING id", aid)
    await c.execute("INSERT INTO sources (track_id, video_id) VALUES ($1, $2)", tid, vid)
    await c.close()
    return tid


def test_miss_returns_youtube_and_marks_in_library(client):
    h = profile(client)
    tid = asyncio.run(_add_source("known000001"))
    FAKE_REMOTE[:] = [cand("known000001", "Zork"), cand("fresh000001", "Fresh")]
    try:
        r = client.get("/api/search", params={"q": "qwxyz nothing"}, headers=h).json()
        by = {x["video_id"]: x for x in r["youtube"]}
        assert by["known000001"]["in_library"] and by["known000001"]["track_id"] == tid
        assert by["fresh000001"]["in_library"] is False and by["fresh000001"]["track_id"] is None
        assert client.get("/api/search", params={"q": "qwxyz nothing", "remote": "never"}, headers=h).json()["youtube"] == []
        assert client.get("/api/search", params={"q": "qwxyz nothing", "type": "artists"}, headers=h).json()["youtube"] == []
    finally:
        FAKE_REMOTE.clear()


def test_suggest_queries_cached_and_failure_not_cached(client, monkeypatch):
    from app import youtube
    h = profile(client)
    monkeypatch.setattr(youtube, "_ytm_suggest", lambda q: [f"{q} {i}" for i in range(8)])
    assert client.get("/api/suggest/queries", params={"q": "Qsug One"}, headers=h).json() == [f"qsug one {i}" for i in range(6)]
    assert client.get("/api/suggest/queries", params={"q": "q"}, headers=h).json() == []

    def down(q):
        raise RuntimeError("offline")
    monkeypatch.setattr(youtube, "_ytm_suggest", down)
    assert len(client.get("/api/suggest/queries", params={"q": "qsug one"}, headers=h).json()) == 6  # served from Redis
    assert client.get("/api/suggest/queries", params={"q": "qsug two"}, headers=h).json() == []
    monkeypatch.setattr(youtube, "_ytm_suggest", lambda q: ["back"])
    assert client.get("/api/suggest/queries", params={"q": "qsug two"}, headers=h).json() == ["back"]  # the failure wasn't cached


def test_hit_skips_youtube_unless_always_and_drops_listed_songs(client):
    h = profile(client)
    asyncio.run(_add_source("known000002"))
    FAKE_REMOTE[:] = [cand("known000002", "Zork"), cand("fresh000002", "Fresh")]
    try:
        auto = client.get("/api/search", params={"q": "zork"}, headers=h).json()
        assert auto["cache_hit"] and auto["youtube"] == []
        always = client.get("/api/search", params={"q": "zork", "remote": "always"}, headers=h).json()
        assert [y["video_id"] for y in always["youtube"]] == ["fresh000002"]  # the known song is already in `songs`
    finally:
        FAKE_REMOTE.clear()
