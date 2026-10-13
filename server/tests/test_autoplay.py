"""D51: the autoplay trigger (ws.py), the job (autoplay.py), failures and isolation. Tavily, NVIDIA and YouTube search are stubbed."""
import asyncio
import json
import uuid

import httpx
import pytest
from arq.jobs import Job

from app import autoplay, config, worker, youtube
from app.text import normalize_query
from tests.helpers import mk_track, prof, run
from tests.test_worker import drain, with_ctx
from tests.test_ws import conn, quiet, until


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nv-test-key")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")  # a real key in .env must never be used here; the Gemini tests set a fake one


# ------------------------------------------------------------------ the trigger

def state(ctype, cid, tracks, pos, playing=True, frm="context", order=None):
    return {"context": {"type": ctype, "id": cid, "name": f"{ctype} {cid}"}, "context_tracks": tracks, "order": order if order is not None else list(range(len(tracks))),
            "context_pos": pos, "user_queue": [], "current": {"track_id": tracks[pos] if tracks else 1, "from": frm}, "shuffle": order is not None,
            "repeat": "off", "position_ms": 0, "playing": playing}


def jid(pid, ctype, cid, n):
    return f"ap:{pid}:{ctype}:{cid}:{n}"


def queued(client, job_id):
    return client.portal.call(client.app.state.redis.zscore, "arq:queue", job_id) is not None


def job_args(client, job_id):
    async def info():
        return await Job(job_id, client.app.state.arq).info()
    return client.portal.call(info).args


def job_kwargs(client, job_id):
    async def info():
        return await Job(job_id, client.app.state.arq).info()
    return client.portal.call(info).kwargs


def new_profile(client):
    h = prof(client, "ap")
    return h, int(h["X-Profile-Id"]), "dev" + uuid.uuid4().hex[:8]


def test_last_item_of_a_radio_context_fires_with_the_right_arguments(client):
    h, pid, dev = new_profile(client)
    a = mk_track(client, h)
    client.post("/api/me/history", json=[{"track_id": a["id"], "played_at": "2026-10-12T10:00:00Z", "ms_played": 60_000, "context": "search"}], headers=h)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        ws.send_json({"type": "queue_update", "state": state("radio", "77", [77, 78, 79], 2)})
        time_wait = quiet(ws)  # lets the server finish handling the message
    assert queued(client, jid(pid, "radio", "77", 3))
    p, ctx, after_len, seed, exclude = job_args(client, jid(pid, "radio", "77", 3))
    assert (p, ctx, after_len, seed) == (pid, "radio:77", 3, 79) and {77, 78, 79, a["id"]} <= set(exclude)   # the whole list and recent history are excluded


@pytest.mark.parametrize("label,st,patch_prefs", [
    ("middle item", state("radio", "1", [1, 2, 3], 1), None),
    ("paused", state("radio", "2", [1, 2], 1, playing=False), None),
    ("user_queue item", state("radio", "3", [1, 2], 1, frm="user"), None),
    ("search context", state("search", "", [1, 2], 1), None),
    ("queue context", state("queue", "", [1, 2], 1), None),
    ("radio, autoplay off", state("radio", "4", [1, 2], 1), {"autoplay": False}),
    ("playlist, autoplay off", state("playlist", "9", [1, 2], 1), {"autoplay": False}),
])
def test_does_not_fire(client, label, st, patch_prefs):
    h, pid, dev = new_profile(client)
    if patch_prefs:
        client.patch("/api/me", json={"prefs": patch_prefs}, headers=h)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        ws.send_json({"type": "queue_update", "state": st})
        quiet(ws)
    assert not queued(client, jid(pid, st["context"]["type"], st["context"]["id"], len(st["context_tracks"]))), label


def test_playlist_album_artist_liked_fire_only_with_autoplay_on(client):
    h, pid, dev = new_profile(client)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        for ctype, cid in [("playlist", "5"), ("album", "6"), ("artist", "7"), ("liked", "")]:   # prefs.autoplay is unset: that means on
            ws.send_json({"type": "queue_update", "state": state(ctype, cid, [1, 2], 1)})
        quiet(ws)
        assert all(queued(client, jid(pid, t, c, 2)) for t, c in [("playlist", "5"), ("album", "6"), ("artist", "7"), ("liked", "")])
        client.patch("/api/me", json={"prefs": {"autoplay": False}}, headers=h)
        ws.send_json({"type": "queue_update", "state": state("playlist", "8", [1, 2], 1)})
        quiet(ws)
    assert not queued(client, jid(pid, "playlist", "8", 2))
    assert job_args(client, jid(pid, "playlist", "5", 2))[1] == "playlist:5"


def test_same_state_five_times_is_one_job_and_growth_fires_again(client):
    h, pid, dev = new_profile(client)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        for _ in range(5):
            ws.send_json({"type": "queue_update", "state": state("radio", "10", [1, 2, 3], 2)})
            ws.send_json({"type": "player_state", "track_id": 3, "position_ms": 1000, "playing": True, "at": 1})   # heartbeats and seeks too
        quiet(ws)
        assert queued(client, jid(pid, "radio", "10", 3))
        client.portal.call(client.app.state.redis.zrem, "arq:queue", jid(pid, "radio", "10", 3))   # the job ran and finished
        ws.send_json({"type": "player_state", "track_id": 3, "position_ms": 2000, "playing": True, "at": 2})
        quiet(ws)
        assert not queued(client, jid(pid, "radio", "10", 3))      # the next heartbeat does not run it all over again
        ws.send_json({"type": "queue_update", "state": state("radio", "10", [1, 2, 3, 4, 5], 4)})   # 2 more songs arrived, and the last one is now playing
        quiet(ws)
    assert queued(client, jid(pid, "radio", "10", 5)) and job_args(client, jid(pid, "radio", "10", 5))[2] == 5


def test_a_new_radio_context_with_only_the_seed_fires_at_once(client):
    h, pid, dev = new_profile(client)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        ws.send_json({"type": "queue_update", "state": state("playlist", "3", [1, 2, 3, 4], 0)})        # the user was playing a playlist...
        ws.send_json({"type": "queue_update", "state": state("radio", "42", [42], 0)})                  # ...then picked song 42 by hand
        quiet(ws)
    assert queued(client, jid(pid, "radio", "42", 1)) and job_args(client, jid(pid, "radio", "42", 1))[3] == 42
    assert not queued(client, jid(pid, "playlist", "3", 4))                                              # a middle item of the playlist: nothing


def test_play_press_after_a_paused_update_fires_via_player_state(client):
    h, pid, dev = new_profile(client)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        ws.send_json({"type": "queue_update", "state": state("radio", "11", [11], 0, playing=False)})
        quiet(ws)
        assert not queued(client, jid(pid, "radio", "11", 1))
        ws.send_json({"type": "player_state", "track_id": 11, "position_ms": 0, "playing": True, "at": 1})   # the user presses play: this device becomes active
        quiet(ws)
    assert queued(client, jid(pid, "radio", "11", 1))


def test_shuffled_order_uses_the_last_position_of_order(client):
    h, pid, dev = new_profile(client)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        ws.send_json({"type": "queue_update", "state": state("playlist", "12", [1, 2, 3], 1, order=[2, 0, 1])})   # position 1 of 3 in a shuffle: not last
        ws.send_json({"type": "queue_update", "state": state("playlist", "13", [1, 2, 3], 2, order=[2, 0, 1])})   # position 2 of 3: last
        quiet(ws)
    assert not queued(client, jid(pid, "playlist", "12", 3)) and queued(client, jid(pid, "playlist", "13", 3))


def test_missing_key_means_no_job(client, monkeypatch):
    h, pid, dev = new_profile(client)
    for name in ("TAVILY_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setattr(config, name, "")
        with conn(client, pid, dev) as ws:
            until(ws, "state")
            ws.send_json({"type": "queue_update", "state": state("radio", f"k{name[:3]}", [1], 0)})
            quiet(ws)
        assert not queued(client, jid(pid, "radio", f"k{name[:3]}", 1))
        monkeypatch.setattr(config, name, "x")


def test_malformed_states_never_break_the_socket(client):
    h, pid, dev = new_profile(client)
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        for bad in [{"playing": True}, {"playing": True, "order": "x", "context_tracks": 5}, {"playing": True, "order": [], "context_tracks": [], "current": {"from": "context"}},
                    {"playing": True, "order": [0], "context_tracks": [1], "context_pos": 0, "current": {"from": "context", "track_id": "nope"}, "context": {"type": "radio"}}]:
            ws.send_json({"type": "queue_update", "state": bad})
        ws.send_json({"type": "ping"})
        assert until(ws, "pong")


# ------------------------------------------------------------------ parsing

def test_parse_titles_handles_fenced_bare_prose_and_cut_off_replies():
    seed = "Boom Shaka"
    assert autoplay.parse_titles('```json\n["Locha-E-Ulfat", "Hookah Bar"]\n```', seed) == ["Locha-E-Ulfat", "Hookah Bar"]
    assert autoplay.parse_titles('["A", "B", "C"]', seed) == ["A", "B", "C"]
    assert autoplay.parse_titles('Sure! Here you go: ["Dilbar", "Kala Chashma"] Hope that helps.', seed) == ["Dilbar", "Kala Chashma"]
    assert autoplay.parse_titles('["Dilbar", "Kala Chashma", "Chalti Hai Kya 9 Se', seed) == ["Dilbar", "Kala Chashma"]     # cut off at the token limit
    assert autoplay.parse_titles("", seed) == [] and autoplay.parse_titles("I could not find any songs.", seed) == []
    assert autoplay.parse_titles('["Boom Shaka", "Boom Shaka (Remix)", "BOOM  shaka!", "Other"]', seed) == ["Other"]        # the seed and anything containing it
    assert autoplay.parse_titles('["Same", "same", " Same ", 5, null, "", "Next"]', seed) == ["Same", "Next"]                # repeats, non-strings, blanks
    assert len(autoplay.parse_titles(json.dumps([f"Song {i}" for i in range(25)]), seed)) == 10


# ------------------------------------------------------------------ the job

def tavily_payload():
    return {"query": "q", "follow_up_questions": None, "answer": None, "images": [], "response_time": 8.2, "request_id": "r-1", "results": [
        {"url": "https://example.com/a", "title": "Songs like Dil Cheez Tujhe Dedi", "content": "Locha-E-Ulfat, Hookah Bar...", "score": 0.9, "raw_content": "R" * 5000},
        {"url": "https://example.com/b", "title": "Party playlist", "content": "Dilbar...", "score": 0.7, "raw_content": None}]}


def nvidia_payload(text):
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": "nvidia/nemotron-3-super-120b-a12b", "usage": {"total_tokens": 900},
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def stub_web(monkeypatch, reply, tavily_status=200, nvidia_status=200, boom=None):
    seen = {"tavily": [], "nvidia": []}
    def handler(req):
        if boom:
            raise boom
        body = json.loads(req.content)
        if req.url.host == "api.tavily.com":
            seen["tavily"].append((body, req.headers["authorization"]))
            return httpx.Response(tavily_status, json=tavily_payload() if tavily_status == 200 else {"detail": "err"})
        seen["nvidia"].append((body, req.headers["authorization"]))
        return httpx.Response(nvidia_status, json=nvidia_payload(reply) if nvidia_status == 200 else {"detail": "err"})
    monkeypatch.setattr(autoplay, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def cand(vid, title, dur=210_000, artist="Some Singer"):
    return {"video_id": vid, "title": title, "artists": [{"name": artist, "ytm_channel_id": None}], "album": None, "duration_ms": dur,
            "thumb_url": None, "channel": None, "quality_score": 4.5, "from_ytm": True, "view_count": None}


def stub_search(monkeypatch, table):
    async def search(redis, nq, merge=False):
        v = table.get(nq)
        if isinstance(v, Exception):
            raise v
        return list(v or [])
    monkeypatch.setattr(youtube, "search", search)


def vid(tag):
    return ("a" + tag + uuid.uuid4().hex)[:11]


async def run_job(pid, context_key, after_len, seed, exclude, method="tavily"):
    async def go(ctx):
        ps = ctx["redis"].pubsub()
        await ps.subscribe(f"ws:profile:{pid}")
        await ps.get_message(timeout=1)
        res = await autoplay.autoplay_extend(ctx, pid, context_key, after_len, seed, exclude, method=method)
        return res, await drain(ps)
    return await with_ctx(go)


def seed_track(client, h, title="Dil Cheez Tujhe Dedi", artist="Ankit Tiwari"):
    vv = vid("s")
    return client.post("/api/tracks/ensure", json=[{"video_id": vv, "title": title, "artists": [{"name": artist}], "duration_ms": 240_000}], headers=h).json()[0]


def test_job_end_to_end_order_filters_and_event(client, monkeypatch):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h)
    already = mk_track(client, h, title="Played Before")              # in the exclude list: must not come back
    v = {k: vid(k) for k in "abcdefg"}
    stub_search(monkeypatch, {
        "locha e ulfat": [cand(v["a"], "Locha-E-Ulfat")],
        "hookah bar": [cand(v["b"], "Hookah Bar")],
        "long mix": [cand(v["c"], "Two Hour Mix", dur=7_200_000)],                  # over ten minutes
        "played before": [cand(already["video_id"], "Played Before")],               # already in the context/history
        "dilbar": [cand(v["d"], "Dilbar")],
        "dilbar again": [cand(v["d"], "Dilbar")],                                     # the same video twice
        "no such song": [],                                                          # nothing found
        "search breaks": RuntimeError("ytmusicapi down"),
        "kala chashma": [cand(v["e"], "Kala Chashma")],
    })
    reply = '```json\n' + json.dumps(["Locha-E-Ulfat", "Hookah Bar", "Long Mix", "Played Before", "Dilbar", "Dilbar Again", "No Such Song", "Search Breaks",
                                      "Dil Cheez Tujhe Dedi (Remix)", "Kala Chashma"]) + '\n```'
    seen = stub_web(monkeypatch, reply)
    res, events = asyncio.run(run_job(pid, f"radio:{seed['id']}", 1, seed["id"], [already["id"]]))
    assert len(events) == 1 and events[0]["type"] == "autoplay_tracks"
    e = events[0]
    assert e["context"] == {"type": "radio", "id": str(seed["id"])} and e["after_len"] == 1 and e["seed_track_id"] == seed["id"]
    assert [t["title"] for t in e["tracks"]] == ["Locha-E-Ulfat", "Hookah Bar", "Dilbar", "Kala Chashma"]          # the model's order, filters applied
    assert all(t["id"] and t["video_id"] and t["cache_state"] == "remote" for t in e["tracks"]) and e["stats"] == {"titles": 9, "resolved": 7, "kept": 4, "examples": [seed["id"]], "method": "tavily"}
    assert res == {"tracks": 4, "titles": 9, "resolved": 7, "kept": 4, "examples": [seed["id"]], "method": "tavily"}
    assert run(lambda c: c.fetchval("SELECT count(*) FROM sources WHERE video_id = ANY($1)", [v["a"], v["b"], v["d"], v["e"]])) == 4   # saved as provisional tracks
    assert run(lambda c: c.fetchval("SELECT count(*) FROM sources WHERE video_id = $1", v["c"])) == 0                                 # the long one was never saved
    # what was sent to Tavily and the model
    (tb, tauth), (nb, nauth) = seen["tavily"][0], seen["nvidia"][0]
    assert tb == {"query": "Dil Cheez Tujhe Dedi Ankit Tiwari similar songs", "search_depth": "advanced", "max_results": 10, "include_raw_content": "text"} and tauth == "Bearer tvly-test-key"
    assert nauth == "Bearer nv-test-key" and nb["model"] == config.NVIDIA_MODEL == "nvidia/nemotron-3-super-120b-a12b"
    assert (nb["temperature"], nb["max_tokens"], nb["chat_template_kwargs"]) == (0, 300, {"enable_thinking": False}) and len(nb["messages"]) == 1 and nb["messages"][0]["role"] == "user"
    prompt = nb["messages"][0]["content"]
    assert 'similar to "Dil Cheez Tujhe Dedi"' in prompt and "JSON array of strings" in prompt and "Locha-E-Ulfat, Hookah Bar" in prompt
    assert "R" * 3000 in prompt and "R" * 3001 not in prompt                                    # only the first 3000 characters of the raw page
    ttl = client.portal.call(client.app.state.redis.ttl, f"ap:titles:{seed['id']}")
    assert 6 * 86400 < ttl <= 7 * 86400


def test_cache_hit_skips_the_web_and_empty_answers_are_not_cached(client, monkeypatch):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h, title="Tu Jaane Na", artist="Atif Aslam")
    v = vid("c")
    stub_search(monkeypatch, {"dilbar": [cand(v, "Dilbar")]})
    seen = stub_web(monkeypatch, '["Dilbar"]')
    asyncio.run(run_job(pid, f"radio:{seed['id']}", 1, seed["id"], []))
    assert len(seen["tavily"]) == 1 and len(seen["nvidia"]) == 1
    _, events = asyncio.run(run_job(pid, f"radio:{seed['id']}", 1, seed["id"], []))
    assert len(seen["tavily"]) == 1 and len(seen["nvidia"]) == 1                              # the titles came from Redis
    assert [t["title"] for t in events[0]["tracks"]] == ["Dilbar"]
    # an answer with no titles is published (empty) but never cached
    empty_seed = seed_track(client, h, title="Boom Shaka", artist="KR$NA")
    seen = stub_web(monkeypatch, "I could not find any similar songs.")
    _, ev = asyncio.run(run_job(pid, f"radio:{empty_seed['id']}", 1, empty_seed["id"], []))
    assert ev[0]["tracks"] == [] and client.portal.call(client.app.state.redis.exists, f"ap:titles:{empty_seed['id']}") == 0
    asyncio.run(run_job(pid, f"radio:{empty_seed['id']}", 1, empty_seed["id"], []))
    assert len(seen["tavily"]) == 2                                                           # so the next try asks again


@pytest.mark.parametrize("label,kw", [
    ("tavily 500", {"tavily_status": 500}), ("nvidia 500", {"nvidia_status": 500}), ("timeout", {"boom": httpx.ReadTimeout("slow")}),
    ("connection error", {"boom": httpx.ConnectError("down")}), ("garbage reply", {"reply": "}{"}),
])
def test_failures_publish_an_empty_event_and_never_raise(client, monkeypatch, label, kw):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h, title=f"Fail Song {label}")
    stub_search(monkeypatch, {})
    stub_web(monkeypatch, **{"reply": '["X"]', **kw})
    res, events = asyncio.run(run_job(pid, f"radio:{seed['id']}", 3, seed["id"], []))               # no exception escaped
    assert len(events) == 1 and events[0]["tracks"] == [] and events[0]["after_len"] == 3 and events[0]["context"]["type"] == "radio"
    assert res["tracks"] == 0


def test_job_without_keys_or_seed_publishes_empty(client, monkeypatch):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h)
    seen = stub_web(monkeypatch, '["X"]')
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "")
    _, ev = asyncio.run(run_job(pid, f"radio:{seed['id']}", 1, seed["id"], []))
    assert ev[0]["tracks"] == [] and seen == {"tavily": [], "nvidia": []}                               # no call was made
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nv-test-key")
    _, ev = asyncio.run(run_job(pid, "radio:999999999", 1, 999999999, []))
    assert ev[0]["tracks"] == []                                                                         # the seed track no longer exists


def test_the_job_is_registered_without_retries():
    fn = next(f for f in worker.Settings.functions if getattr(f, "name", "") == "autoplay_extend")
    assert fn.max_tries == 1 and fn.timeout_s >= 120                                                     # a retry would spend credits again


def test_the_event_reaches_only_the_owning_profile(client, monkeypatch):
    a, pa, da = new_profile(client)
    b, pb, db = new_profile(client)
    seed = seed_track(client, a)
    v = vid("i")
    stub_search(monkeypatch, {"dilbar": [cand(v, "Dilbar")]})
    stub_web(monkeypatch, '["Dilbar"]')
    with conn(client, pa, da) as wa, conn(client, pb, db) as wb:
        until(wa, "state"); until(wb, "state")
        asyncio.run(run_job(pa, f"radio:{seed['id']}", 1, seed["id"], []))
        got = until(wa, "autoplay_tracks")
        assert got["tracks"][0]["title"] == "Dilbar" and got["seed_track_id"] == seed["id"]
        assert not [m for m in quiet(wb) if m["type"] == "autoplay_tracks"]


# ------------------------------------------------------------------ batch 2 onward: three songs of the previous batch in ONE query

def run_batch(client, h, pid, ctx, after_len, seed_id, exclude=()):
    return asyncio.run(run_job(pid, ctx, after_len, seed_id, list(exclude)))


def make_batch(monkeypatch, n, tag):
    """Search table + model reply for a batch of n songs named '<tag> k'."""
    vids = [vid(f"{tag}{k}") for k in range(n)]
    stub_search(monkeypatch, {f"{tag} song {k}": [cand(vids[k], f"{tag} Song {k}", artist=f"{tag} Singer {k}")] for k in range(n)})
    return [f"{tag} Song {k}" for k in range(n)]


def test_second_batch_puts_three_songs_of_the_first_in_one_query(client, monkeypatch):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h)
    names = make_batch(monkeypatch, 6, "alpha")
    seen = stub_web(monkeypatch, json.dumps(names))
    _, ev1 = run_batch(client, h, pid, f"radio:{seed['id']}", 1, seed["id"])
    first = ev1[0]
    assert [t["title"] for t in first["tracks"]] == names and first["stats"]["examples"] == [seed["id"]]
    assert len(seen["tavily"]) == 1 and seen["tavily"][0][0]["query"] == "Dil Cheez Tujhe Dedi Ankit Tiwari similar songs"   # batch 1: just the seed
    ids = [seed["id"]] + [t["id"] for t in first["tracks"]]
    last = ids[-1]
    monkeypatch.setattr(autoplay, "sample", lambda pop, k: [pop[4], pop[0], pop[2]])             # a fixed "random" choice
    names2 = make_batch(monkeypatch, 4, "beta")
    seen2 = stub_web(monkeypatch, json.dumps(names2 + ["alpha Song 4 (Live)"]))
    _, ev2 = run_batch(client, h, pid, f"radio:{seed['id']}", len(ids), last, ids)
    second = ev2[0]
    # exactly one Tavily call and one model call, with all three examples in each
    assert len(seen2["tavily"]) == 1 and len(seen2["nvidia"]) == 1
    picked = [first["tracks"][4], first["tracks"][0], first["tracks"][2]]
    assert seen2["tavily"][0][0]["query"] == ", ".join(f"{t['title']} {t['artists'][0]['name']}" for t in picked) + " similar songs"
    assert second["stats"]["examples"] == [t["id"] for t in picked]
    prompt = seen2["nvidia"][0][0]["messages"][0]["content"]
    assert 'similar to "alpha Song 4", "alpha Song 0" and "alpha Song 2"' in prompt and "Do not include any of those songs themselves." in prompt
    assert [t["title"] for t in second["tracks"]] == names2                                       # "alpha Song 4 (Live)" contains an example's title: dropped
    assert last not in {t["id"] for t in second["tracks"]}                                         # and the song that is playing never comes back


def test_examples_are_random_distinct_and_from_the_last_batch(client):
    batch = list(range(100, 110))
    picks = [autoplay.sample(batch, 3) for _ in range(40)]
    assert all(len(set(p)) == 3 and set(p) <= set(batch) for p in picks) and len({tuple(sorted(p)) for p in picks}) > 5   # varies from call to call


def test_small_batches_use_every_song_and_unusable_records_fall_back_to_the_seed(client, monkeypatch):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h, title="Small Seed", artist="Small Artist")
    names = make_batch(monkeypatch, 2, "gamma")                                                   # only two songs came back
    stub_web(monkeypatch, json.dumps(names))
    _, ev1 = run_batch(client, h, pid, f"radio:{seed['id']}", 1, seed["id"])
    ids = [seed["id"]] + [t["id"] for t in ev1[0]["tracks"]]
    names2 = make_batch(monkeypatch, 2, "delta")
    seen = stub_web(monkeypatch, json.dumps(names2))
    _, ev2 = run_batch(client, h, pid, f"radio:{seed['id']}", len(ids), ids[-1], ids)
    assert sorted(ev2[0]["stats"]["examples"]) == sorted(ids[1:])                                  # fewer than three: all of them
    assert seen["tavily"][0][0]["query"].count("similar songs") == 1 and "gamma Song 0" in seen["tavily"][0][0]["query"] and "gamma Song 1" in seen["tavily"][0][0]["query"]
    # the client's list is not "seed + that batch" (it dropped or changed it): only the playing song is known
    seen = stub_web(monkeypatch, json.dumps(make_batch(monkeypatch, 2, "eps")))
    _, ev3 = run_batch(client, h, pid, f"radio:{seed['id']}", 99, ids[-1], ids)
    assert ev3[0]["stats"]["examples"] == [ids[-1]] and len(seen["tavily"]) == 1
    # a different radio has no record either
    _, ev4 = run_batch(client, h, pid, f"radio:{ids[-1]}", len(ids), ids[-1], ids)
    assert ev4[0]["stats"]["examples"] == [ids[-1]]


def test_a_playlist_batch_becomes_the_example_pool_of_the_radio_that_follows(client, monkeypatch):
    h, pid, _ = new_profile(client)
    last = seed_track(client, h, title="Last Of The Playlist", artist="PL Artist")                  # the last playlist track is the seed
    names = make_batch(monkeypatch, 5, "zeta")
    stub_web(monkeypatch, json.dumps(names))
    _, ev1 = run_batch(client, h, pid, "playlist:7", 12, last["id"])
    assert ev1[0]["context"] == {"type": "playlist", "id": "7"} and len(ev1[0]["tracks"]) == 5
    new_ids = [t["id"] for t in ev1[0]["tracks"]]
    # the client starts radio:<last track> holding only the 5 new tracks, then plays its last item
    monkeypatch.setattr(autoplay, "sample", lambda pop, k: pop[:k])
    seen = stub_web(monkeypatch, json.dumps(make_batch(monkeypatch, 3, "eta")))
    _, ev2 = run_batch(client, h, pid, f"radio:{last['id']}", 5, new_ids[-1], new_ids)
    assert ev2[0]["stats"]["examples"] == new_ids[:3] and "zeta Song 0" in seen["tavily"][0][0]["query"]


def test_the_same_examples_are_served_from_the_cache_and_a_deleted_example_is_skipped(client, monkeypatch):
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h, title="Cache Seed", artist="Cache Artist")
    names = make_batch(monkeypatch, 4, "theta")
    stub_web(monkeypatch, json.dumps(names))
    _, ev1 = run_batch(client, h, pid, f"radio:{seed['id']}", 1, seed["id"])
    ids = [seed["id"]] + [t["id"] for t in ev1[0]["tracks"]]
    monkeypatch.setattr(autoplay, "sample", lambda pop, k: pop[:k])
    seen = stub_web(monkeypatch, json.dumps(make_batch(monkeypatch, 3, "iota")))
    run_batch(client, h, pid, f"radio:{seed['id']}", len(ids), ids[-1], ids)
    assert len(seen["tavily"]) == 1
    key = "ap:titles:" + "+".join(str(i) for i in sorted(ev1[0]["tracks"][i]["id"] for i in range(3)))
    assert client.portal.call(client.app.state.redis.exists, key) == 1                             # keyed by the set of example songs
    # the same record again (e.g. a retried trigger) picks the same three songs: no new web call
    redis = client.app.state.redis
    client.portal.call(redis.set, batch_key_for(pid, seed["id"]), json.dumps({"len": len(ids), "ids": ids[1:]}))
    seen2 = stub_web(monkeypatch, "[]")
    run_batch(client, h, pid, f"radio:{seed['id']}", len(ids), ids[-1], ids)
    assert seen2 == {"tavily": [], "nvidia": []}
    # an example track that has since been deleted is skipped; if none is left the playing song is the example
    run(lambda c: c.execute("DELETE FROM tracks WHERE id = ANY($1)", ids[1:4]))
    seen3 = stub_web(monkeypatch, json.dumps(make_batch(monkeypatch, 2, "kappa")))
    client.portal.call(redis.set, batch_key_for(pid, seed["id"]), json.dumps({"len": len(ids), "ids": ids[1:4]}))
    _, ev = run_batch(client, h, pid, f"radio:{seed['id']}", len(ids), ids[-1], ids)
    assert ev[0]["stats"]["examples"] == [ids[-1]] and len(seen3["tavily"]) == 1


def batch_key_for(pid, seed_id):
    return autoplay.batch_key(pid, "radio", str(seed_id))


# ------------------------------------------------------------------ the Gemini method and choosing a method

def stub_gemini(monkeypatch, reply, status=200, busy=()):
    seen = []
    def handler(req):
        assert req.url.host == "generativelanguage.googleapis.com"   # no Tavily or NVIDIA call on this path
        seen.append((req.url.path, req.headers["x-goog-api-key"], json.loads(req.content)))
        if any(f"/{m}:" in req.url.path for m in busy):
            return httpx.Response(503, json={"error": {"code": 503, "message": "This model is currently experiencing high demand."}})
        parts = [{"text": '["Thought Song - Nobody"]', "thought": True}, {"text": reply}]
        return httpx.Response(status, json={"candidates": [{"content": {"role": "model", "parts": parts}}]} if status == 200 else {"error": {"code": status}})
    monkeypatch.setattr(autoplay, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def test_gemini_job_asks_the_model_alone_and_caches_per_method(client, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gm-test-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-test-lite")
    h, pid, _ = new_profile(client)
    seed = seed_track(client, h, title="Gemini Seed Song", artist="Seed Singer")
    a, b = vid("ga"), vid("gb")
    stub_search(monkeypatch, {normalize_query("Locha-E-Ulfat - Amit Trivedi"): [cand(a, "Locha-E-Ulfat")],
                              normalize_query("Hookah Bar - Himesh Reshammiya"): [cand(b, "Hookah Bar")]})
    seen = stub_gemini(monkeypatch, json.dumps(["Locha-E-Ulfat - Amit Trivedi", "Gemini Seed Song - Seed Singer", "Hookah Bar - Himesh Reshammiya"]))
    res, ev = asyncio.run(run_job(pid, f"radio:{seed['id']}", 1, seed["id"], [], method="gemini"))
    assert [t["title"] for t in ev[0]["tracks"]] == ["Locha-E-Ulfat", "Hookah Bar"]          # the seed itself and the thought part are dropped
    assert res["method"] == ev[0]["stats"]["method"] == "gemini"
    (path, key, body), = seen
    assert path == "/v1beta/models/gemini-test-lite:generateContent" and key == "gm-test-key" and "tools" not in body
    prompt = body["contents"][0]["parts"][0]["text"]
    assert '"Gemini Seed Song" by Seed Singer' in prompt and "JSON array of strings" in prompt
    redis = client.app.state.redis
    assert client.portal.call(redis.exists, f"ap:titles:gemini:{seed['id']}") == 1 and client.portal.call(redis.exists, f"ap:titles:{seed['id']}") == 0
    asyncio.run(run_job(pid, f"radio:{seed['id']}", 1, seed["id"], [], method="gemini"))
    assert len(seen) == 1                                                                     # served from the cache
    other = seed_track(client, h, title="Gemini Busy Song")
    monkeypatch.setattr(config, "GEMINI_MODEL", "new-lite, old-lite")                          # the newest is busy: the next one answers
    seen = stub_gemini(monkeypatch, json.dumps(["Hookah Bar - Himesh Reshammiya"]), busy=["new-lite"])
    _, ev = asyncio.run(run_job(pid, f"radio:{other['id']}", 1, other["id"], [], method="gemini"))
    assert [p for p, _, _ in seen] == ["/v1beta/models/new-lite:generateContent", "/v1beta/models/old-lite:generateContent"]
    assert [t["title"] for t in ev[0]["tracks"]] == ["Hookah Bar"]
    third = seed_track(client, h, title="Gemini All Busy Song")
    stub_gemini(monkeypatch, "[]", busy=["new-lite", "old-lite"])                                # every model busy: an empty event, no exception
    _, ev = asyncio.run(run_job(pid, f"radio:{third['id']}", 1, third["id"], [], method="gemini"))
    assert ev[0]["tracks"] == []
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")                                          # a method without its key never calls out
    seen = stub_gemini(monkeypatch, "[]")
    asyncio.run(run_job(pid, f"radio:{other['id']}", 1, other["id"], [], method="gemini"))
    assert seen == []


def test_the_profile_picks_the_method_and_settings_lists_the_configured_ones(client, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gm-test-key")
    assert config.autoplay_methods() == ["gemini", "tavily"]
    assert [config.autoplay_method(p) for p in ({}, {"autoplay_method": "tavily"}, {"autoplay_method": "bogus"})] == ["gemini", "tavily", "gemini"]
    h, pid, dev = new_profile(client)
    assert client.get("/api/settings", headers=h).json()["autoplay_methods"] == ["gemini", "tavily"]
    with conn(client, pid, dev) as ws:
        until(ws, "state")
        ws.send_json({"type": "queue_update", "state": state("radio", "m1", [1, 2], 1)})
        quiet(ws)
        client.patch("/api/me", json={"prefs": {"autoplay_method": "tavily"}}, headers=h)
        ws.send_json({"type": "queue_update", "state": state("radio", "m2", [1, 2], 1)})
        quiet(ws)
    assert job_kwargs(client, jid(pid, "radio", "m1", 2)) == {"method": "gemini"}            # the default
    assert job_kwargs(client, jid(pid, "radio", "m2", 2)) == {"method": "tavily"}            # the profile's choice
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    assert config.autoplay_method({"autoplay_method": "tavily"}) == "gemini"                 # a choice whose keys are gone falls back
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert config.autoplay_methods() == [] and config.autoplay_method({}) is None
