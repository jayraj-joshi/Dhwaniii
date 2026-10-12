import time

from tests.helpers import jobs, mk_track, prof, run


def test_like_unlike_flow_and_download_side_effect(client):
    h = prof(client); t = mk_track(client, h)
    assert client.put(f"/api/me/likes/{t['id']}", headers=h).status_code == 204
    assert client.put(f"/api/me/likes/{t['id']}", headers=h).status_code == 204  # idempotent
    assert jobs([t["video_id"]]) == {t["video_id"]: ("queued", "low")}  # saving fetches it, at low priority (D19)
    assert client.portal.call(client.app.state.redis.zscore, "arq:queue", f"dl:{t['video_id']}") is None  # waits in the backlog
    assert client.post("/api/tracks/ensure", json=[{"video_id": t["video_id"], "title": "x"}], headers=h).json()[0]["liked"] is True
    assert client.get(f"/api/tracks/{t['id']}", headers=h).json()["liked"] is True
    assert client.delete(f"/api/me/likes/{t['id']}", headers=h).status_code == 204
    assert client.delete(f"/api/me/likes/{t['id']}", headers=h).status_code == 204
    assert client.get(f"/api/tracks/{t['id']}", headers=h).json()["liked"] is False
    assert client.put("/api/me/likes/99999999", headers=h).status_code == 404
    assert client.put(f"/api/me/likes/{t['id']}").status_code == 401


def test_likes_newest_first_with_paging_and_per_profile(client):
    h1, h2 = prof(client), prof(client)
    ts = [mk_track(client, h1) for _ in range(5)]
    for t in ts:
        client.put(f"/api/me/likes/{t['id']}", headers=h1)
        time.sleep(0.01)
    ids = [x["id"] for x in client.get("/api/me/likes", headers=h1).json()]
    assert ids == [t["id"] for t in reversed(ts)]
    assert [x["id"] for x in client.get("/api/me/likes?offset=1&limit=2", headers=h1).json()] == ids[1:3]
    assert client.get("/api/me/likes", headers=h2).json() == []
    assert client.get("/api/me/likes?limit=0", headers=h1).status_code == 422
    assert client.get("/api/me/likes?limit=201", headers=h1).status_code == 422
    assert all(x["liked"] for x in client.get("/api/me/likes", headers=h1).json())


def test_library_counts_and_orders_playlists(client):
    h = prof(client)
    a = client.post("/api/playlists", json={"name": "A"}, headers=h).json()
    b = client.post("/api/playlists", json={"name": "B"}, headers=h).json()
    c = client.post("/api/playlists", json={"name": "C"}, headers=h).json()
    t = mk_track(client, h); client.put(f"/api/me/likes/{t['id']}", headers=h)
    lib = client.get("/api/me/library", headers=h).json()
    assert lib["liked_count"] == 1 and [p["id"] for p in lib["playlists"]] == [c["id"], b["id"], a["id"]]  # newest edit first
    client.patch(f"/api/playlists/{a['id']}", json={"pinned_to_sidebar": True}, headers=h)
    client.post("/api/me/history", json=[{"track_id": t["id"], "played_at": "2099-01-01T00:00:00Z", "ms_played": 1000, "context": f"playlist:{b['id']}"}], headers=h)
    assert [p["id"] for p in client.get("/api/me/library", headers=h).json()["playlists"]] == [a["id"], b["id"], c["id"]]  # pinned, then last played


def test_recent_contexts(client):
    h = prof(client); t = mk_track(client, h, title="Seed Song")
    pl = client.post("/api/playlists", json={"name": "Road trip"}, headers=h).json()
    gone = client.post("/api/playlists", json={"name": "Gone"}, headers=h).json()
    client.put(f"/api/me/likes/{t['id']}", headers=h)
    play = lambda ctx, at: {"track_id": t["id"], "played_at": at, "ms_played": 1000, "context": ctx}
    client.post("/api/me/history", json=[
        play(f"playlist:{pl['id']}", "2026-10-12T10:00:00Z"), play("liked", "2026-10-12T11:00:00Z"), play("search", "2026-10-12T12:00:00Z"),
        play("queue", "2026-10-12T12:30:00Z"), play(f"radio:{t['id']}", "2026-10-12T09:00:00Z"), play(f"playlist:{gone['id']}", "2026-10-12T08:00:00Z"),
        play(f"playlist:{pl['id']}", "2026-10-12T07:00:00Z"), play("album:987654", "2026-10-12T06:00:00Z")], headers=h)
    client.delete(f"/api/playlists/{gone['id']}", headers=h)
    r = client.get("/api/me/recent", headers=h).json()
    assert [x["context"] for x in r] == ["liked", f"playlist:{pl['id']}", f"radio:{t['id']}"]  # distinct, newest first; search/queue/deleted/unknown dropped
    assert [x["name"] for x in r] == ["Liked Songs", "Road trip", "Seed Song Radio"]
    assert r[0]["kind"] == "liked" and r[0]["id"] is None and r[1]["kind"] == "playlist" and r[1]["id"] == pl["id"]
    assert r[0]["art_url"] == f"/api/art/track/{t['id']}" and r[2]["art_url"] == f"/api/art/track/{t['id']}"
    assert len(client.get("/api/me/recent?limit=1", headers=h).json()) == 1
    assert client.get("/api/me/recent", headers=prof(client)).json() == []


def test_settings_match_files_table(client, monkeypatch):
    from app import config, storage
    h = prof(client)
    base = client.get("/api/settings", headers=h).json()
    assert base["codec"] == config.PREF_CODEC and base["storage_limit_bytes"] == int(config.STORAGE_LIMIT_GB * storage.GB)
    t = mk_track(client, h)
    async def add(c):
        await c.execute("INSERT INTO files (source_id, path, codec, bytes) SELECT id, 'x/' || id, 'm4a', 12345 FROM sources WHERE video_id = $1", t["video_id"])
        return await c.fetchrow("SELECT coalesce(sum(bytes), 0)::bigint AS used, count(*) AS n FROM files")
    row = run(add)
    s = client.get("/api/settings", headers=h).json()
    assert s["storage_used_bytes"] == row["used"] == base["storage_used_bytes"] + 12345 and s["file_count"] == row["n"] == base["file_count"] + 1
    monkeypatch.setattr(config, "STORAGE_LIMIT_GB", 2)
    assert client.get("/api/settings", headers=h).json()["storage_limit_bytes"] == 2 * 1024**3
    assert client.get("/api/settings").status_code == 401


def test_recent_searches(client):
    import time
    h1, h2 = prof(client), prof(client)
    post = lambda h, q: client.post("/api/me/searches", json={"query": q}, headers=h)
    for q in ["tum hi ho", "Kesariya", "  lover   diljit "]:
        assert post(h1, q).status_code == 204
        time.sleep(0.01)
    assert [r["query"] for r in client.get("/api/me/searches", headers=h1).json()] == ["lover diljit", "Kesariya", "tum hi ho"]  # newest first, spaces tidied
    post(h1, "TUM HI HO")                                         # same words, other case: one entry, moved to the top, shown as last typed
    assert [r["query"] for r in client.get("/api/me/searches", headers=h1).json()] == ["TUM HI HO", "lover diljit", "Kesariya"]
    assert client.get("/api/me/searches", headers=h2).json() == []
    assert client.delete("/api/me/searches?query=kesariya", headers=h1).status_code == 204  # one entry, any case
    assert [r["query"] for r in client.get("/api/me/searches", headers=h1).json()] == ["TUM HI HO", "lover diljit"]
    for bad in ["", "   ", "!!!", "x" * 201]:
        assert post(h1, bad).status_code == 422
    assert client.post("/api/me/searches", json={"query": "x"}).status_code == 401
    for i in range(60):
        post(h1, f"query number {i}")
    assert len(client.get("/api/me/searches", headers=h1).json()) == 20
    assert run(lambda c: c.fetchval("SELECT count(*) FROM recent_searches WHERE profile_id = $1", int(h1["X-Profile-Id"]))) == 50  # older ones are pruned
    assert client.delete("/api/me/searches", headers=h1).status_code == 204 and client.get("/api/me/searches", headers=h1).json() == []
    post(h2, "mine"); client.delete("/api/me/searches", headers=h1)
    assert len(client.get("/api/me/searches", headers=h2).json()) == 1  # clearing one profile never touches another
