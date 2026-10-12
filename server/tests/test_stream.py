import asyncio
import concurrent.futures as cf
import re
import uuid

import asyncpg
import httpx
import pytest

from app import config, youtube
from tests.conftest import TEST_DB, _url

DATA = bytes(range(256)) * 40  # 10240 bytes


def profile(client):
    return {"X-Profile-Id": str(client.post("/api/profiles", json={"name": f"s-{uuid.uuid4().hex[:8]}"}).json()["id"])}


def vid():
    return "st" + uuid.uuid4().hex[:9]


def run(coro_fn):
    async def go():
        c = await asyncpg.connect(_url(TEST_DB))
        try:
            return await coro_fn(c)
        finally:
            await c.close()
    return asyncio.run(go())


def upstream(handler_calls):
    def handler(req: httpx.Request) -> httpx.Response:
        handler_calls.append(str(req.url))
        if "expired" in str(req.url):
            return httpx.Response(403)
        m = re.match(r"bytes=(\d+)-(\d*)", req.headers.get("range", "bytes=0-"))
        a = int(m[1]); b = int(m[2]) if m[2] else len(DATA) - 1
        chunk = DATA[a:b + 1]  # a stream, not content=, so it behaves like a real unread upstream response
        return httpx.Response(206, stream=httpx.ByteStream(chunk), headers={
            "content-type": "audio/mp4", "accept-ranges": "bytes", "content-length": str(len(chunk)),
            "content-range": f"bytes {a}-{b}/{len(DATA)}"})
    return handler


@pytest.fixture
def live(client, monkeypatch):
    """Mock upstream + resolver. `calls` = upstream URLs fetched, `resolved` = (video_id, fresh) per resolve."""
    calls, resolved = [], []
    old = client.app.state.http
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream(calls)))

    async def fake_resolve(redis, video_id, fresh=False):
        resolved.append((video_id, fresh))
        return {"url": f"https://gv.example/{'ok' if fresh or len(resolved) > 1 else 'expired'}/{video_id}" if EXPIRE else f"https://gv.example/ok/{video_id}",
                "http_headers": {"User-Agent": "t"}, "mime": "audio/mp4"}
    monkeypatch.setattr(youtube, "resolve_audio_url", fake_resolve)
    yield calls, resolved
    client.app.state.http = old


EXPIRE = False


def test_cached_file_served_with_ranges_and_play_counted_once(client, tmp_path, monkeypatch):
    h, v = profile(client), vid()
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    rel = f"{v[:2]}/{v[2:4]}/{v}.m4a"
    (tmp_path / v[:2] / v[2:4]).mkdir(parents=True)
    (tmp_path / rel).write_bytes(DATA)

    async def seed(c):
        a = await c.fetchval("INSERT INTO artists (name, name_norm) VALUES ('A', $1) RETURNING id", v)
        t = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id) VALUES ('T', 't', $1) RETURNING id", a)
        s = await c.fetchval("INSERT INTO sources (track_id, video_id) VALUES ($1, $2) RETURNING id", t, v)
        await c.execute("INSERT INTO files (source_id, path, codec, bytes) VALUES ($1, $2, 'm4a', $3)", s, rel, len(DATA))
        return s
    sid = run(seed)
    last = lambda: run(lambda c: c.fetchval("SELECT last_played FROM files WHERE source_id = $1", sid))

    r = client.get(f"/api/stream/{v}", headers=h)
    assert r.status_code == 200 and r.content == DATA and r.headers["content-type"] == "audio/mp4" and last() is not None
    run(lambda c: c.execute("UPDATE files SET last_played = NULL"))
    r = client.get(f"/api/stream/{v}", headers={**h, "Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == DATA[100:200] and last() is None  # later chunks don't count as plays
    client.get(f"/api/stream/{v}", headers={**h, "Range": "bytes=0-"})
    assert last() is not None
    assert client.get(f"/api/stream/{v}").status_code == 401


def test_download_sends_attachment_header_with_artist_and_title(client, tmp_path, monkeypatch):
    h, v = profile(client), vid()
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    rel = f"{v[:2]}/{v[2:4]}/{v}.m4a"
    (tmp_path / v[:2] / v[2:4]).mkdir(parents=True)
    (tmp_path / rel).write_bytes(DATA)

    async def seed(c):
        a = await c.fetchval("INSERT INTO artists (name, name_norm) VALUES ('The Band', $1) RETURNING id", v)
        t = await c.fetchval("INSERT INTO tracks (title, title_norm, primary_artist_id) VALUES ('A Song', 't', $1) RETURNING id", a)
        s = await c.fetchval("INSERT INTO sources (track_id, video_id) VALUES ($1, $2) RETURNING id", t, v)
        await c.execute("INSERT INTO track_artists (track_id, artist_id, role, position) VALUES ($1, $2, 'primary', 0)", t, a)
        await c.execute("INSERT INTO files (source_id, path, codec, bytes) VALUES ($1, $2, 'm4a', $3)", s, rel, len(DATA))
    run(seed)

    r = client.get(f"/api/stream/{v}?download=1", headers=h)
    assert r.status_code == 200 and r.content == DATA
    assert r.headers["content-disposition"] == 'attachment; filename="The Band - A Song.m4a"'


def test_download_of_uncached_track_is_409(client):
    h, v = profile(client), vid()
    assert client.get(f"/api/stream/{v}?download=1", headers=h).status_code == 409


def test_miss_streams_live_creates_one_job_and_one_arq_job(client, live):
    calls, resolved = live
    h, v = profile(client), vid()
    r = client.get(f"/api/stream/{v}", headers={**h, "Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == DATA[10:20] and r.headers["content-range"] == f"bytes 10-19/{len(DATA)}"
    assert r.headers["content-type"] == "audio/mp4"
    with cf.ThreadPoolExecutor(6) as ex:  # several devices ask for the same uncached video at once
        codes = list(ex.map(lambda _: client.get(f"/api/stream/{v}", headers=h).status_code, range(6)))
    assert set(codes) == {200}  # no Range header from the client means a plain 200
    jobs = run(lambda c: c.fetch("SELECT status, priority FROM download_jobs WHERE video_id = $1", v))
    assert [(j["status"], j["priority"]) for j in jobs] == [("queued", "high")]
    assert client.portal.call(client.app.state.redis.zscore, "arq:queue", f"dl:{v}") is not None
    assert client.portal.call(client.app.state.redis.zcount, "arq:queue", "-inf", "+inf") >= 1
    assert client.portal.call(client.app.state.redis.exists, f"dl:lock:{v}") == 1


def test_expired_url_is_re_resolved_once(client, live, monkeypatch):
    calls, resolved = live
    global EXPIRE
    EXPIRE = True
    try:
        h, v = profile(client), vid()
        r = client.get(f"/api/stream/{v}", headers={**h, "Range": "bytes=0-9"})
        assert r.status_code == 206 and r.content == DATA[:10]
        assert [x[1] for x in resolved] == [False, True] and "expired" in calls[0] and "/ok/" in calls[1]
    finally:
        EXPIRE = False


def test_unavailable_video_is_404(client, monkeypatch):
    async def boom(redis, video_id, fresh=False):
        raise youtube.Unavailable("Private video")
    monkeypatch.setattr(youtube, "resolve_audio_url", boom)
    assert client.get(f"/api/stream/{vid()}", headers=profile(client)).status_code == 404


def test_cache_endpoint_priorities(client):
    h, v = profile(client), vid()
    rd = client.app.state.redis
    assert client.post(f"/api/tracks/{v}/cache?priority=low", headers=h).json() == {"cache_state": "downloading"}
    assert client.portal.call(rd.zscore, "arq:queue", f"dl:{v}") is None  # low priority waits in the DB backlog
    assert client.post(f"/api/tracks/{v}/cache?priority=high", headers=h).json() == {"cache_state": "downloading"}
    assert client.portal.call(rd.zscore, "arq:queue", f"dl:{v}") is not None  # promoted: now in arq
    assert run(lambda c: c.fetchval("SELECT priority FROM download_jobs WHERE video_id = $1", v)) == "high"
    assert client.post(f"/api/tracks/{v}/cache?priority=high", headers=h).status_code == 200  # idempotent
    assert client.post(f"/api/tracks/{v}/cache?priority=urgent", headers=h).status_code == 422
    assert client.post("/api/tracks/bad/cache", headers=h).status_code == 422


def test_failed_job_not_requeued_by_low_priority_but_retried_by_high(client):
    h, v = profile(client), vid()
    run(lambda c: c.execute("INSERT INTO download_jobs (video_id, status, priority, attempts, error) VALUES ($1, 'failed', 'low', 4, 'x')", v))
    assert client.post(f"/api/tracks/{v}/cache?priority=low", headers=h).json() == {"cache_state": "failed"}
    assert client.post(f"/api/tracks/{v}/cache?priority=high", headers=h).json() == {"cache_state": "downloading"}
    assert run(lambda c: c.fetchrow("SELECT status, attempts, error FROM download_jobs WHERE video_id = $1", v))["attempts"] == 0
