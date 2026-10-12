"""M4.3-B: one profile's likes, history, playlists, queue, searches, follows, imports, devices and offline marks never reach another's."""
import uuid

from tests.helpers import mk_track, prof, run
from tests.test_ws import conn, until, quiet


def test_profile_b_sees_nothing_of_profile_a(client):
    a, b = prof(client, "iso-a"), prof(client, "iso-b")
    pa, pb = int(a["X-Profile-Id"]), int(b["X-Profile-Id"])
    word = "iso" + uuid.uuid4().hex[:6]
    t1, t2 = mk_track(client, a, title=f"{word} one"), mk_track(client, a, title=f"{word} one")

    # --- profile A uses every feature -------------------------------------------------------
    client.patch("/api/me", json={"prefs": {"theme": "dark", "crossfade_s": 6}, "avatar": "🎧"}, headers=a)
    client.put(f"/api/me/likes/{t1['id']}", headers=a)
    client.post("/api/me/history", json=[{"track_id": t2["id"], "played_at": "2026-10-12T10:00:00Z", "ms_played": 90_000, "context": "liked"}] * 25, headers=a)
    pl = client.post("/api/playlists", json={"name": f"{word} private mix"}, headers=a).json()
    client.post(f"/api/playlists/{pl['id']}/tracks", json={"track_ids": [t1["id"]]}, headers=a)
    client.post("/api/me/searches", json={"query": f"{word} secret search"}, headers=a)
    artist = t1["artists"][0]["id"]
    client.put(f"/api/me/follows/{artist}", headers=a)
    alb = run(lambda c: c.fetchval("INSERT INTO albums (title, title_norm, artist_id) VALUES ('Iso Album', 'iso album', $1) RETURNING id", artist))
    client.put(f"/api/me/albums/{alb}", headers=a)
    imp = client.post("/api/import", json={"url": "https://youtube.com/playlist?list=PLisolation12345"}, headers=a).json()
    dev_a = "isoA" + uuid.uuid4().hex[:6]
    queue = {"context_tracks": [t1["id"]], "current": {"track_id": t1["id"], "from": "context"}, "position_ms": 12_000, "playing": True}
    with conn(client, pa, dev_a, "A's phone") as wa:
        until(wa, "state")
        wa.send_json({"type": "queue_update", "state": queue})
        wa.send_json({"type": "player_state", "track_id": t1["id"], "position_ms": 12_000, "playing": True, "at": 1})
        until(wa, "active_device")
        client.put(f"/api/me/downloads/{pl['id']}", params={"device": dev_a}, headers=a)

        # --- profile B looks everywhere -----------------------------------------------------
        assert client.get("/api/me", headers=b).json()["prefs"] == {} and client.get("/api/me", headers=b).json()["avatar"] == ""
        assert client.get("/api/me/likes", headers=b).json() == []
        lib = client.get("/api/me/library", headers=b).json()
        assert lib == {"liked_count": 0, "playlists": [], "artists": [], "albums": []}
        assert client.get("/api/me/recent", headers=b).json() == [] and client.get("/api/me/searches", headers=b).json() == []
        assert client.get("/api/playlists", headers=b).json() == []
        for method, path, kw in [("get", f"/api/playlists/{pl['id']}", {}), ("patch", f"/api/playlists/{pl['id']}", {"json": {"name": "mine now"}}),
                                 ("delete", f"/api/playlists/{pl['id']}", {}), ("get", f"/api/imports/{imp['import_id']}", {}),
                                 ("get", "/api/me/downloads", {"params": {"device": dev_a}}), ("put", f"/api/me/downloads/{pl['id']}", {"params": {"device": dev_a}})]:
            assert client.request(method, path, headers=b, **kw).status_code == 404, (method, path)
        assert client.get("/api/devices", headers=b).json() == []
        s = client.get("/api/search", params={"q": word}, headers=b).json()
        assert s["playlists"] == [] and [x["liked"] for x in s["songs"]] == [False, False]        # my playlist and my likes are not theirs
        assert [x["id"] for x in s["songs"]] == [min(t1["id"], t2["id"]), max(t1["id"], t2["id"])]  # and 25 plays of mine did not reorder their results
        assert [x["id"] for x in client.get("/api/search", params={"q": word}, headers=a).json()["songs"]][0] == t2["id"]   # (for me they did)
        feed = {x["id"]: x for x in client.get("/api/home", headers=b).json()["sections"]}
        assert "on_repeat" not in feed and "jump_back_in" not in feed and feed["quick_access"]["items"][0]["art_url"] is None
        assert client.get(f"/api/artists/{artist}", headers=b).json()["followed"] is False and client.get(f"/api/albums/{alb}", headers=b).json()["saved"] is False
        assert client.get(f"/api/artists/{artist}", headers=b).json()["plays_30d"] == 0
        # B's own queue and devices are empty, and A's socket hears nothing from B
        with conn(client, pb, "isoB" + uuid.uuid4().hex[:6], "B's laptop") as wb:
            st = until(wb, "state")
            assert st["state"] is None and st["active_device_id"] is None and [d["name"] for d in st["devices"]] == ["B's laptop"]
            wb.send_json({"type": "queue_update", "state": {"x": 1}})
            wb.send_json({"type": "player_state", "track_id": 9, "position_ms": 1, "playing": True, "at": 2})
            wb.send_json({"type": "transfer", "to_device": dev_a})
            assert until(wb, "error")
            assert not [m for m in quiet(wa) if m["type"] in ("queue_update", "player_state", "active_device")]
        # B changing things leaves A alone
        client.patch("/api/me", json={"prefs": {"theme": "light"}, "name": "iso-b-renamed"}, headers=b)
        client.post("/api/me/searches", json={"query": "b only"}, headers=b)
    assert client.get("/api/me", headers=a).json()["prefs"] == {"theme": "dark", "crossfade_s": 6}
    assert [x["query"] for x in client.get("/api/me/searches", headers=a).json()] == [f"{word} secret search"]
    assert run(lambda c: c.fetchval("SELECT state FROM playback_state WHERE profile_id = $1", pa)) is not None   # A's queue survived
    assert client.get("/api/me/likes", headers=a).json()[0]["id"] == t1["id"] and client.get(f"/api/playlists/{pl['id']}", headers=a).status_code == 200


def test_header_wins_over_query_param_and_both_are_per_request(client):
    a, b = prof(client, "hdr-a"), prof(client, "hdr-b")
    assert client.get(f"/api/me?profile={b['X-Profile-Id']}", headers=a).json()["id"] == int(a["X-Profile-Id"])
    assert client.get(f"/api/me?profile={b['X-Profile-Id']}").json()["id"] == int(b["X-Profile-Id"])
