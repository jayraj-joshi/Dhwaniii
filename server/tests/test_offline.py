"""M4.2-B: marking playlists for offline use pins their files against eviction."""
import asyncio
import uuid

import pytest

from app import config, storage
from tests.helpers import jobs, mk_track, prof, run
from tests.test_worker import with_ctx


def device(h, name="Phone"):
    d = "od" + uuid.uuid4().hex[:8]
    run(lambda c: c.execute("INSERT INTO devices (id, profile_id, name) VALUES ($1, $2, $3)", d, int(h["X-Profile-Id"]), name))
    return d


def mkpl(client, h, tracks=()):
    pl = client.post("/api/playlists", json={"name": "Offline"}, headers=h).json()
    if tracks:
        client.post(f"/api/playlists/{pl['id']}/tracks", json={"track_ids": [t["id"] for t in tracks]}, headers=h)
    return pl


def test_mark_list_unmark_and_ownership(client):
    h, other = prof(client), prof(client); d = device(h); od = device(other)
    ts = [mk_track(client, h) for _ in range(2)]
    pl = mkpl(client, h, ts)
    url = f"/api/me/downloads/{pl['id']}"
    assert client.put(url, params={"device": d}, headers=h).status_code == 204 and client.put(url, params={"device": d}, headers=h).status_code == 204  # idempotent
    assert set(jobs([t["video_id"] for t in ts]).values()) == {("queued", "low")}   # the files must exist on the server
    got = client.get("/api/me/downloads", params={"device": d}, headers=h).json()
    assert [(x["id"], x["track_count"]) for x in got] == [(pl["id"], 2)] and got[0]["marked_at"]
    assert client.get(f"/api/playlists/{pl['id']}", headers=h).json()["offline_devices"] == [d]
    assert client.get("/api/me/downloads", params={"device": device(h, "Laptop")}, headers=h).json() == []     # per device
    # not your device, not your playlist, unknown device
    assert client.put(url, params={"device": od}, headers=h).status_code == 404
    assert client.put(url, params={"device": "nope"}, headers=h).status_code == 404
    assert client.put(url, params={"device": od}, headers=other).status_code == 404                           # their device, my playlist
    assert client.get("/api/me/downloads", params={"device": d}, headers=other).status_code == 404
    assert client.put(url, headers=h).status_code == 422 and client.put(url, params={"device": d}).status_code == 401
    assert client.delete(url, params={"device": d}, headers=other).status_code == 204                          # silently nothing: it isn't theirs
    assert len(client.get("/api/me/downloads", params={"device": d}, headers=h).json()) == 1
    assert client.delete(url, params={"device": d}, headers=h).status_code == 204 and client.delete(url, params={"device": d}, headers=h).status_code == 204
    assert client.get("/api/me/downloads", params={"device": d}, headers=h).json() == []


def seed_files(root, vids, hours_old):
    """A file on disk and a files row per video, oldest first by `hours_old`."""
    async def go(c):
        for v, age in zip(vids, hours_old):
            rel = f"off/{v}.m4a"; (root / "off").mkdir(exist_ok=True); (root / rel).write_bytes(b"x" * 1000)
            await c.execute("INSERT INTO files (source_id, path, codec, bytes, created_at) SELECT id, $2, 'm4a', 1000, now() - $3 * interval '1 hour' FROM sources WHERE video_id = $1", v, rel, age)
    run(go)


def evict(monkeypatch, limit_bytes):
    monkeypatch.setattr(config, "STORAGE_LIMIT_GB", limit_bytes / storage.GB)
    return asyncio.run(with_ctx(lambda ctx: storage.evict(ctx["pool"])))


def left(vids):
    return {r["video_id"] for r in run(lambda c: c.fetch("SELECT s.video_id FROM files f JOIN sources s ON s.id = f.source_id WHERE s.video_id = ANY($1)", vids))}


def test_files_of_offline_playlists_survive_eviction(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    run(lambda c: c.execute("DELETE FROM files"))                       # a clean slate: only this test's files count
    h = prof(client); d = device(h)
    a, b, c_ = (mk_track(client, h) for _ in range(3))
    pl = mkpl(client, h, [a])
    client.put(f"/api/me/downloads/{pl['id']}", params={"device": d}, headers=h)
    vids = [a["video_id"], b["video_id"], c_["video_id"]]
    seed_files(tmp_path, vids, [30, 20, 10])                            # a is the oldest, c the newest
    res = evict(monkeypatch, 2000)                                      # 3000 bytes against 2000: down to 1800
    assert left(vids) == {a["video_id"]} and res["evicted"] == 2        # b and c go; the offline one stays even though it is the oldest
    assert (tmp_path / f"off/{a['video_id']}.m4a").exists() and not (tmp_path / f"off/{b['video_id']}.m4a").exists()
    assert run(lambda c: c.fetchval("SELECT count(*) FROM tracks WHERE id = ANY($1)", [a["id"], b["id"], c_["id"]])) == 3   # track rows stay
    assert evict(monkeypatch, 500)["over_budget"] is True               # everything left is pinned: reported, not deleted
    assert left(vids) == {a["video_id"]}


def test_pin_follows_the_playlist_unmarking_and_deleting(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    run(lambda c: c.execute("DELETE FROM files"))
    h = prof(client); d = device(h)
    a, b = mk_track(client, h), mk_track(client, h)
    pl = mkpl(client, h, [a])
    client.put(f"/api/me/downloads/{pl['id']}", params={"device": d}, headers=h)
    client.post(f"/api/playlists/{pl['id']}/tracks", json={"track_ids": [b["id"]]}, headers=h)   # added after marking: pinned too
    seed_files(tmp_path, [a["video_id"], b["video_id"]], [20, 10])
    assert evict(monkeypatch, 1)["evicted"] == 0 and left([a["video_id"], b["video_id"]]) == {a["video_id"], b["video_id"]}
    client.delete(f"/api/me/downloads/{pl['id']}", params={"device": d}, headers=h)             # unmarked: evictable again
    assert evict(monkeypatch, 1)["evicted"] == 2
    seed_files(tmp_path, [a["video_id"]], [5])
    client.put(f"/api/me/downloads/{pl['id']}", params={"device": d}, headers=h)
    client.delete(f"/api/playlists/{pl['id']}", headers=h)                                      # deleting the playlist drops the mark
    assert run(lambda c: c.fetchval("SELECT count(*) FROM device_downloads WHERE playlist_id = $1", pl["id"])) == 0
    assert evict(monkeypatch, 1)["evicted"] == 1


def test_a_track_in_two_playlists_stays_pinned_until_both_are_unmarked(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    run(lambda c: c.execute("DELETE FROM files"))
    h = prof(client); d1, d2 = device(h), device(h, "Laptop")
    t = mk_track(client, h); p1, p2 = mkpl(client, h, [t]), mkpl(client, h, [t])
    for pl, d in ((p1, d1), (p2, d2)):
        client.put(f"/api/me/downloads/{pl['id']}", params={"device": d}, headers=h)
    seed_files(tmp_path, [t["video_id"]], [5])
    client.delete(f"/api/me/downloads/{p1['id']}", params={"device": d1}, headers=h)
    assert evict(monkeypatch, 1)["evicted"] == 0                                                 # still kept offline by the other device
    client.delete(f"/api/me/downloads/{p2['id']}", params={"device": d2}, headers=h)
    assert evict(monkeypatch, 1)["evicted"] == 1
