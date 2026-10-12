"""M4.4-B: admin status, retry, next-best source, and the yt-dlp update."""
import asyncio
import uuid

import pytest

from app import admin, config, storage, worker
from tests.conftest import FAKE_REMOTE
from tests.helpers import jobs, mk_track, prof, run
from tests.test_worker import with_ctx


def vid():
    return "ad" + uuid.uuid4().hex[:9]


def test_status_counts_failures_and_disk(client, tmp_path, monkeypatch):
    h = prof(client); t = mk_track(client, h); liked = mk_track(client, h)
    run(lambda c: c.execute("DELETE FROM files"))
    run(lambda c: c.execute("DELETE FROM download_jobs"))
    client.put(f"/api/me/likes/{liked['id']}", headers=h)                       # queues a low job for it
    f1, f2, other = vid(), vid(), vid()
    async def seed(c):
        await c.execute("INSERT INTO download_jobs (video_id, status, priority, attempts, error) VALUES ($1, 'failed', 'high', 4, 'HTTP Error 403'), ($2, 'failed', 'low', 1, 'Private video'), ($3, 'running', 'high', 1, NULL)", t["video_id"], f1, f2)
        await c.execute("INSERT INTO files (source_id, path, codec, bytes) SELECT id, 'a/' || id, 'm4a', 700 FROM sources WHERE video_id = $1", liked["video_id"])
        await c.execute("INSERT INTO files (source_id, path, codec, bytes) SELECT id, 'b/' || id, 'm4a', 300 FROM sources WHERE video_id = $1", t["video_id"])
    run(seed)
    monkeypatch.setattr(config, "STORAGE_LIMIT_GB", 500 / storage.GB)
    s = client.get("/api/admin/status", headers=h).json()
    assert s["jobs"] == {"queued": 1, "running": 1, "done": 0, "failed": 2} and s["active_by_priority"] == {"high": 1, "low": 1}
    byv = {f["video_id"]: f for f in s["failed"]}
    assert byv[t["video_id"]]["track"] == {"id": t["id"], "title": t["title"], "artist": t["artists"][0]["name"]} and byv[t["video_id"]]["error"] == "HTTP Error 403" and byv[t["video_id"]]["attempts"] == 4
    assert byv[f1]["track"] is None                                              # a job for a video we never made a track for
    assert s["disk"] == {"used_bytes": 1000, "limit_bytes": 500, "pinned_bytes": 700, "over_budget": True, "file_count": 2}
    assert s["yt_dlp_version"] and client.get("/api/admin/status").status_code == 401


def test_retry_requeues_a_failed_job_at_high_priority(client):
    h = prof(client); v = vid()
    run(lambda c: c.execute("INSERT INTO download_jobs (video_id, status, priority, attempts, error) VALUES ($1, 'failed', 'low', 4, 'boom')", v))
    assert client.post(f"/api/admin/jobs/{v}/retry", headers=h).json() == {"cache_state": "downloading"}
    r = run(lambda c: c.fetchrow("SELECT status, priority, attempts, error FROM download_jobs WHERE video_id = $1", v))
    assert (r["status"], r["priority"], r["attempts"], r["error"]) == ("queued", "high", 0, None)
    assert client.portal.call(client.app.state.redis.zscore, "arq:queue", f"dl:{v}") is not None
    assert client.post("/api/admin/jobs/nosuchvideo/retry", headers=h).status_code == 404


def cand(v, dur=200_000, title="Same Song"):
    return {"video_id": v, "title": title, "artists": [{"name": "X", "ytm_channel_id": None}], "album": None, "duration_ms": dur, "thumb_url": None,
            "channel": "Chan", "quality_score": 0, "from_ytm": True, "view_count": 10}


def test_alternative_switches_to_the_next_best_upload(client):
    h = prof(client); t = mk_track(client, h, title="Same Song", duration_ms=200_000); mine = t["video_id"]
    other_track = mk_track(client, h)
    run(lambda c: c.execute("INSERT INTO download_jobs (video_id, status, priority, attempts, error) VALUES ($1, 'failed', 'high', 4, 'removed')", mine))
    good, far = vid(), vid()
    FAKE_REMOTE[:] = [cand(mine), cand(other_track["video_id"]), cand(far, dur=500_000), cand(good, dur=201_000)]   # the broken one, another track's, a wrong length, the right one
    try:
        r = client.post(f"/api/admin/jobs/{mine}/alternative", headers=h)
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == t["id"] and d["video_id"] == good                      # the failed upload is skipped, other tracks' are never taken
        assert {s["video_id"] for s in d["sources"]} == {mine, good}
        assert jobs([good]) == {good: ("queued", "high")} and client.portal.call(client.app.state.redis.zscore, "arq:queue", f"dl:{good}") is not None
        FAKE_REMOTE[:] = [cand(mine), cand(good)]                                # nothing new left to try
        assert client.post(f"/api/admin/jobs/{mine}/alternative", headers=h).status_code == 409
    finally:
        FAKE_REMOTE.clear()
    assert client.post("/api/admin/jobs/neverseen00/alternative", headers=h).status_code == 404


@pytest.fixture
def updater(monkeypatch):
    calls = {"exit": 0, "pip": 0}
    def fake_pip():
        calls["pip"] += 1
        return {"from": "2026.08.19", "to": "2026.10.01"}
    monkeypatch.setattr(admin, "pip_update", fake_pip)
    monkeypatch.setattr(admin, "exit_process", lambda: calls.__setitem__("exit", calls["exit"] + 1))
    monkeypatch.setattr(admin, "exit_soon", lambda delay=1.0: admin.exit_process())
    return calls


def test_update_ytdlp_updates_here_asks_the_worker_and_restarts(client, updater):
    h = prof(client)
    client.portal.call(client.app.state.redis.zrem, "arq:queue", "update_ytdlp")
    r = client.post("/api/admin/ytdlp/update", headers=h)
    assert r.status_code == 200 and r.json() == {"from": "2026.08.19", "to": "2026.10.01", "restarting": True}
    assert updater == {"exit": 1, "pip": 1}
    assert client.portal.call(client.app.state.redis.zscore, "arq:queue", "update_ytdlp") is not None   # the worker was asked too
    assert client.post("/api/admin/ytdlp/update").status_code == 401


def test_failed_update_reports_and_does_not_restart(client, monkeypatch, updater):
    def boom():
        raise RuntimeError("network unreachable")
    monkeypatch.setattr(admin, "pip_update", boom)
    r = client.post("/api/admin/ytdlp/update", headers=prof(client))
    assert r.status_code == 502 and "network unreachable" in r.text and updater["exit"] == 0


def test_worker_update_job(client, updater):
    res = asyncio.run(with_ctx(lambda ctx: worker.update_ytdlp(ctx)))
    assert res == {"from": "2026.08.19", "to": "2026.10.01"} and updater == {"exit": 1, "pip": 1}


def test_pip_update_reports_a_failed_install(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 2, "", "error: no network\n"))
    with pytest.raises(RuntimeError, match="no network"):
        admin.pip_update()
