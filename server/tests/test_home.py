"""M3.3-B: each home section is built from the data that defines it (checked with known history)."""
from datetime import datetime, timedelta, timezone

from tests.helpers import mk_track, prof, run


def ago(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def plays(client, h, track, n, ms=60_000, days=0, context=None):
    body = [{"track_id": track["id"], "played_at": ago(days), "ms_played": ms, "context": context} for _ in range(n)]
    assert client.post("/api/me/history", json=body, headers=h).status_code == 204


def section(feed, sid):
    return next((s for s in feed["sections"] if s["id"] == sid), None)


def test_new_profile_only_has_the_liked_tile(client):
    feed = client.get("/api/home", headers=prof(client)).json()
    ids = [s["id"] for s in feed["sections"]]
    assert ids[0] == "quick_access" and not {"jump_back_in", "on_repeat"} & set(ids)  # nothing personal yet (recently_added is household-wide)
    assert [(i["kind"], i["name"], i["art_url"]) for i in feed["sections"][0]["items"]] == [("liked", "Liked Songs", None)]
    assert client.get("/api/home").status_code == 401


def test_on_repeat_counts_only_recent_counted_plays(client):
    h = prof(client)
    a, b, c, d, old, short = (mk_track(client, h, duration_ms=200_000) for _ in range(6))
    plays(client, h, a, 5); plays(client, h, b, 3); plays(client, h, c, 2); plays(client, h, d, 1)
    plays(client, h, old, 9, days=40)                      # older than 30 days
    plays(client, h, short, 9, ms=10_000)                  # never reached the 30 s mark
    feed = client.get("/api/home", headers=h).json()
    assert [t["id"] for t in section(feed, "on_repeat")["items"]] == [a["id"], b["id"], c["id"], d["id"]]
    other = client.get("/api/home", headers=prof(client)).json()  # someone else's plays are not yours
    assert section(other, "on_repeat") is None


def test_short_tracks_count_at_half_their_length(client):
    h = prof(client); t = mk_track(client, h, duration_ms=40_000)
    plays(client, h, t, 2, ms=20_000)
    assert [x["id"] for x in section(client.get("/api/home", headers=h).json(), "on_repeat")["items"]] == [t["id"]]


def test_jump_back_in_and_quick_access(client):
    h = prof(client); t = mk_track(client, h)
    pls = [client.post("/api/playlists", json={"name": f"P{i}"}, headers=h).json() for i in range(7)]
    client.put(f"/api/me/likes/{t['id']}", headers=h)
    for i, pl in enumerate(pls):
        plays(client, h, t, 1, days=7 - i, context=f"playlist:{pl['id']}")   # P6 is the newest
    plays(client, h, t, 1, days=0.001, context="liked")
    feed = client.get("/api/home", headers=h).json()
    quick = section(feed, "quick_access")["items"]
    assert [i["kind"] for i in quick][0] == "liked" and quick[0]["art_url"] == f"/api/art/track/{t['id']}"
    assert [i["name"] for i in quick[1:]] == ["P6", "P5", "P4", "P3", "P2"] and len(quick) == 6   # Liked Songs + the five newest, no duplicate liked tile
    jump = section(feed, "jump_back_in")["items"]
    assert [i["context"] for i in jump][:3] == ["liked", f"playlist:{pls[6]['id']}", f"playlist:{pls[5]['id']}"] and len(jump) == 8


def test_recently_added_is_newest_files_first(client):
    h = prof(client); ts = [mk_track(client, h) for _ in range(3)]
    async def add(c):
        for i, t in enumerate(ts):
            await c.execute("INSERT INTO files (source_id, path, codec, bytes, created_at) SELECT id, $2, 'm4a', 1, now() + $3 * interval '1 hour' FROM sources WHERE video_id = $1",
                            t["video_id"], f"x/{t['video_id']}", i + 1000)  # far in the future so these are the newest in the shared test DB
    run(add)
    items = section(client.get("/api/home", headers=h).json(), "recently_added")["items"]
    assert [t["id"] for t in items][:3] == [ts[2]["id"], ts[1]["id"], ts[0]["id"]] and items[0]["cache_state"] == "cached"
