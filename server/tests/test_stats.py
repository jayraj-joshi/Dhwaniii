"""M5.5-B: /api/me/stats and /api/me/wrapped, checked against a history whose numbers are known."""
import uuid
from datetime import datetime, timedelta, timezone

from tests.helpers import prof


def mk(client, h, title, artists, album=None, dur=200_000):
    vid = "st" + uuid.uuid4().hex[:9]
    hint = {"video_id": vid, "title": title, "artists": [{"name": a} for a in artists], "duration_ms": dur}
    if album:
        hint["album"] = {"title": album}
    return client.post("/api/tracks/ensure", json=[hint], headers=h).json()[0]


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def play(client, h, track, secs, at, n=1, skipped=False):
    body = [{"track_id": track["id"], "played_at": at, "ms_played": secs * 1000, "skipped": skipped} for _ in range(n)]
    assert client.post("/api/me/history", json=body, headers=h).status_code == 204


def names(rows):
    return [(r["artist"]["name"], r["plays"]) for r in rows]


def test_stats_per_range(client):
    h = prof(client); u = uuid.uuid4().hex[:6]
    X, Y, Z = f"X{u}", f"Y{u}", f"Z{u}"
    A, B = mk(client, h, "Track A", [X]), mk(client, h, "Track B", [X])
    C, D = mk(client, h, "Track C", [Y]), mk(client, h, "Track D", [Y, Z])            # D credits a featured artist too
    play(client, h, A, 60, ago(3), n=3); play(client, h, A, 12, ago(3))                # 3 counted + one short play (minutes only)
    play(client, h, B, 60, ago(20), n=2); play(client, h, B, 6, ago(3), skipped=True)
    play(client, h, C, 60, ago(100), n=5)
    play(client, h, D, 60, ago(300))
    play(client, h, A, 60, ago(800), n=4)

    s = client.get("/api/me/stats", headers=h).json()                                  # default range: 4 weeks
    assert s["range"] == "4w" and s["since"] is not None
    assert (s["plays"], s["skips"], s["minutes_listened"], s["days_listened"]) == (5, 1, 5.3, 2)   # 318 s of listening, 5 counted plays
    assert [(t["track"]["title"], t["plays"], t["minutes"]) for t in s["top_tracks"]] == [("Track A", 3, 3.2), ("Track B", 2, 2.1)]
    assert names(s["top_artists"]) == [(X, 5)] and s["top_tracks"][0]["track"]["art_url"].startswith("/api/art/track/")

    s = client.get("/api/me/stats", params={"range": "6m"}, headers=h).json()
    assert s["plays"] == 10 and [t["plays"] for t in s["top_tracks"]] == [5, 3, 2] and s["top_tracks"][0]["track"]["title"] == "Track C"
    assert names(s["top_artists"]) == [(X, 5), (Y, 5)]                                  # tie on plays: more listening time first

    s = client.get("/api/me/stats", params={"range": "1y"}, headers=h).json()
    assert s["plays"] == 11 and names(s["top_artists"]) == [(Y, 6), (X, 5), (Z, 1)]     # the featured artist is credited with their play

    s = client.get("/api/me/stats", params={"range": "all"}, headers=h).json()
    assert s["since"] is None and s["plays"] == 15 and s["top_tracks"][0]["plays"] == 7 and names(s["top_artists"])[0] == (X, 9)
    assert len(client.get("/api/me/stats", params={"range": "all"}, headers=h).json()["top_tracks"]) == 4

    assert client.get("/api/me/stats", params={"range": "2y"}, headers=h).status_code == 422
    assert client.get("/api/me/stats").status_code == 401


def test_stats_are_per_profile_and_empty_is_zero(client):
    a, b = prof(client), prof(client)
    t = mk(client, a, "Mine", ["Someone"])
    play(client, a, t, 60, ago(1), n=4)
    assert client.get("/api/me/stats", headers=a).json()["plays"] == 4
    e = client.get("/api/me/stats", params={"range": "all"}, headers=b).json()
    assert (e["plays"], e["skips"], e["minutes_listened"], e["days_listened"], e["top_tracks"], e["top_artists"]) == (0, 0, 0.0, 0, [], [])


def test_only_counted_plays_rank(client):
    h = prof(client)
    t = mk(client, h, "Skimmed", ["Skim Artist"]); short = mk(client, h, "Short Track", ["Short Artist"], dur=40_000)
    play(client, h, t, 29, ago(1), n=50)                       # 29 s of a 200 s track never counts
    play(client, h, short, 20, ago(1), n=2)                    # half of a 40 s track does
    s = client.get("/api/me/stats", headers=h).json()
    assert [x["track"]["title"] for x in s["top_tracks"]] == ["Short Track"] and s["plays"] == 2 and s["minutes_listened"] == round((50 * 29 + 40) / 60, 1)


def test_wrapped_year(client):
    h = prof(client); u = uuid.uuid4().hex[:6]
    X, Y, W = f"X{u}", f"Y{u}", f"W{u}"
    A, B = mk(client, h, "Song A", [X], album=f"Alb1 {u}"), mk(client, h, "Song B", [X], album=f"Alb1 {u}")
    C, E = mk(client, h, "Song C", [Y], album=f"Alb2 {u}"), mk(client, h, "Song E", [W])
    play(client, h, B, 60, "2023-12-31T20:00:00+00:00")                          # X was already known before 2024
    for day in ("2024-03-01", "2024-03-02", "2024-03-03"):
        play(client, h, A, 60, f"{day}T10:00:00+00:00")
    play(client, h, A, 60, "2024-03-10T09:00:00+00:00", n=2); play(client, h, B, 60, "2024-03-10T18:00:00+00:00")
    play(client, h, C, 60, "2024-06-15T12:00:00+00:00")
    play(client, h, E, 10, "2024-07-01T12:00:00+00:00")                          # a skim: listening time and a day, but no play
    r = client.get("/api/me/wrapped", params={"year": 2024}, headers=h).json()
    assert (r["year"], r["plays"], r["minutes_listened"]) == (2024, 7, 7.2)
    cards = {c["id"]: c for c in r["cards"]}
    assert [c["id"] for c in r["cards"]] == ["minutes", "top_song", "top_songs", "top_artist", "top_artists", "top_album", "busiest_day", "discoveries"]
    m = cards["minutes"]
    assert (m["days_listened"], m["longest_streak_days"], m["minutes_per_active_day"], m["share_of_year"], m["skips"]) == (6, 3, 1.2, 0.016, 0) and m["title"] == "Your 2024 in music"
    assert cards["top_song"]["track"]["title"] == "Song A" and cards["top_song"]["plays"] == 5
    assert [t["track"]["title"] for t in cards["top_songs"]["items"]] == ["Song A", "Song C", "Song B"]      # B and C tie on plays and time: the later one first
    assert (cards["top_artist"]["artist"]["name"], cards["top_artist"]["plays"], cards["top_artist"]["minutes"]) == (X, 6, 6.0)
    assert cards["top_album"]["album"]["title"] == f"Alb1 {u}" and cards["top_album"]["plays"] == 6 and cards["top_album"]["album"]["artist"] == X
    assert cards["busiest_day"] == {"id": "busiest_day", "title": "Your biggest listening day", "date": "2024-03-10", "minutes": 3.0}
    assert cards["discoveries"]["count"] == 1                                                              # Y is new in 2024; X was heard in 2023, W never counted

    old = client.get("/api/me/wrapped", params={"year": 2023}, headers=h).json()
    assert old["plays"] == 1 and [c["id"] for c in old["cards"]][0] == "minutes" and {c["id"]: c for c in old["cards"]}["discoveries"]["count"] == 1
    empty = client.get("/api/me/wrapped", params={"year": 2020}, headers=h).json()
    assert empty == {"year": 2020, "plays": 0, "minutes_listened": 0.0, "cards": []}
    assert client.get("/api/me/wrapped", headers=h).json()["year"] == datetime.now(timezone.utc).year           # default: this year
    assert client.get("/api/me/wrapped", params={"year": 1999}, headers=h).status_code == 422 and client.get("/api/me/wrapped", params={"year": 2101}, headers=h).status_code == 422
    assert client.get("/api/me/wrapped", params={"year": 2024}, headers=prof(client)).json()["cards"] == []     # someone else's year is empty
    assert client.get("/api/me/wrapped").status_code == 401


def test_wrapped_without_albums_skips_that_card(client):
    h = prof(client)
    t = mk(client, h, "No Album", ["Loner"])
    play(client, h, t, 60, "2022-05-05T10:00:00+00:00", n=2)
    ids = [c["id"] for c in client.get("/api/me/wrapped", params={"year": 2022}, headers=h).json()["cards"]]
    assert "top_album" not in ids and ids[0] == "minutes" and ids[-1] == "discoveries"
