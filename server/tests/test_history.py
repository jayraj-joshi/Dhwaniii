import asyncio
import uuid

import asyncpg
import pytest

from tests.conftest import TEST_DB, _url


def run(fn):
    async def go():
        c = await asyncpg.connect(_url(TEST_DB))
        try:
            return await fn(c)
        finally:
            await c.close()
    return asyncio.run(go())


def prof(client):
    return {"X-Profile-Id": str(client.post("/api/profiles", json={"name": f"h-{uuid.uuid4().hex[:8]}"}).json()["id"])}


def track(client, h, duration_ms, title="Hist Song"):
    vid = "hs" + uuid.uuid4().hex[:9]
    t = client.post("/api/tracks/ensure", json=[{"video_id": vid, "title": title, "artists": [{"name": f"Artist {vid}"}], "duration_ms": duration_ms}], headers=h).json()[0]
    return t["id"]


def play(tid, ms, skipped=False, at="2026-10-12T10:00:00Z", context="search"):
    return {"track_id": tid, "played_at": at, "ms_played": ms, "skipped": skipped, "context": context, "device_id": "dev1"}


def stats(pid, tid):
    return run(lambda c: c.fetchrow("SELECT play_count, skip_count, last_played FROM user_track_stats WHERE profile_id = $1 AND track_id = $2", pid, tid))


@pytest.mark.parametrize("duration,ms,counted", [
    (200_000, 29_999, 0), (200_000, 30_000, 1),   # long track: 30 s
    (40_000, 19_999, 0), (40_000, 20_000, 1),     # short track: half of it
    (None, 29_999, 0), (None, 30_000, 1),         # unknown duration: 30 s
])
def test_counted_threshold(client, duration, ms, counted):
    h = prof(client); tid = track(client, h, duration)
    assert client.post("/api/me/history", json=[play(tid, ms)], headers=h).status_code == 204
    s = stats(int(h["X-Profile-Id"]), tid)
    assert s["play_count"] == counted and s["skip_count"] == 0
    assert run(lambda c: c.fetchval("SELECT count(*) FROM play_history WHERE track_id = $1", tid)) == 1  # every play is logged, counted or not


def test_batch_accumulates_skips_and_last_played(client):
    h = prof(client); pid = int(h["X-Profile-Id"]); a, b = track(client, h, 200_000), track(client, h, 200_000)
    batch = [play(a, 60_000, at="2026-10-12T10:00:00Z"), play(a, 5_000, skipped=True, at="2026-10-12T11:00:00Z"),
             play(b, 100_000, at="2026-10-12T09:00:00Z", context="playlist:7")]
    assert client.post("/api/me/history", json=batch, headers=h).status_code == 204
    assert client.post("/api/me/history", json=[play(a, 45_000, at="2026-10-12T08:00:00Z")], headers=h).status_code == 204
    sa, sb = stats(pid, a), stats(pid, b)
    assert (sa["play_count"], sa["skip_count"]) == (2, 1) and sa["last_played"].hour == 11  # latest wins, even from an older play
    assert (sb["play_count"], sb["skip_count"]) == (1, 0)


def test_unknown_track_rejects_whole_batch(client):
    h = prof(client); pid = int(h["X-Profile-Id"]); good = track(client, h, 200_000)
    r = client.post("/api/me/history", json=[play(good, 60_000), play(99_999_999, 60_000)], headers=h)
    assert r.status_code == 422 and "99999999" in r.text
    assert stats(pid, good) is None and run(lambda c: c.fetchval("SELECT count(*) FROM play_history WHERE profile_id = $1", pid)) == 0


def test_validation_and_isolation(client):
    h1, h2 = prof(client), prof(client); tid = track(client, h1, 200_000)
    assert client.post("/api/me/history", json=[], headers=h1).status_code == 204
    assert client.post("/api/me/history", json=[play(tid, 1, context="bogus")], headers=h1).status_code == 422
    assert client.post("/api/me/history", json=[play(tid, -1)], headers=h1).status_code == 422
    assert client.post("/api/me/history", json=[play(tid, 1)] * 501, headers=h1).status_code == 422
    assert client.post("/api/me/history", json=[play(tid, 1)]).status_code == 401
    for ctx in ["playlist:1", "album:2", "artist:3", "radio:4", "liked", "search", "queue"]:
        assert client.post("/api/me/history", json=[play(tid, 1, context=ctx)], headers=h1).status_code == 204
    assert client.post("/api/me/history", json=[play(tid, 60_000)], headers=h2).status_code == 204  # profile 2 may play any track
    assert stats(int(h1["X-Profile-Id"]), tid)["play_count"] == 0 and stats(int(h2["X-Profile-Id"]), tid)["play_count"] == 1


def test_plays_raise_search_rank(client):
    h = prof(client); word = "zqx" + uuid.uuid4().hex[:6]
    a, b = track(client, h, 200_000, title=f"{word} one"), track(client, h, 200_000, title=f"{word} one")
    order = lambda: [t["id"] for t in client.get("/api/search", params={"q": f"{word} one", "type": "songs"}, headers=h).json()["songs"]]
    assert order()[0] == min(a, b)  # tie: lowest id
    client.post("/api/me/history", json=[play(max(a, b), 60_000)] * 20, headers=h)
    assert order()[0] == max(a, b)
