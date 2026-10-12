"""M1.9: hub behaviour with real sockets (Starlette TestClient) and real Redis pub/sub."""
import json
import time
import uuid

import anyio
import asyncpg
import asyncio
import pytest
from starlette.websockets import WebSocketDisconnect

from app import ws as wsmod
from tests.conftest import TEST_DB, _url


def mk(client):
    return client.post("/api/profiles", json={"name": f"ws-{uuid.uuid4().hex[:8]}"}).json()["id"]


def conn(client, pid, dev, name="Device"):
    return client.websocket_connect(f"/ws?profile={pid}&device={dev}&name={name}")


def until(ws, kind, **match):
    """Read messages until one has this type (and fields); returns it. Fails after 40 messages instead of hanging."""
    for _ in range(40):
        m = ws.receive_json()
        if m["type"] == kind and all(m.get(k) == v for k, v in match.items()):
            return m
    raise AssertionError(f"no {kind} {match} received")


def quiet(ws):
    """Everything the socket receives up to a pong. Only meaningful after a short pause so in-flight messages have landed."""
    time.sleep(0.4)
    ws.send_json({"type": "ping"})
    out = []
    while (m := ws.receive_json())["type"] != "pong":
        out.append(m)
    return out


def dev():
    return "d" + uuid.uuid4().hex[:10]


def test_connect_sends_state_then_broadcasts_devices(client):
    pid, a, b = mk(client), dev(), dev()
    with conn(client, pid, a, "Laptop") as wa:
        m = wa.receive_json()
        devs = m.pop("devices")
        assert m == {"type": "state", "state": None, "active_device_id": None}
        assert [(d["id"], d["name"], d["online"], d["active"]) for d in devs] == [(a, "Laptop", True, False)] and "T" in devs[0]["last_seen"]
        until(wa, "devices")
        with conn(client, pid, b, "Phone") as wb:
            s = wb.receive_json()
            assert s["type"] == "state" and {d["id"]: d["online"] for d in s["devices"]} == {a: True, b: True}
            assert any(m["type"] == "devices" and {d["id"] for d in m["list"]} == {a, b} for m in quiet(wa))  # A hears about B
        # B left: A hears that B is offline
        got = until(wa, "devices")
        assert {d["id"]: d["online"] for d in got["list"]}[b] is False


def test_ping_pong_and_garbage_ignored(client):
    pid = mk(client)
    with conn(client, pid, dev()) as w:
        w.receive_json()
        w.send_text("not json"); w.send_json({"type": "nonsense"}); w.send_json([1, 2])
        w.send_json({"type": "ping"})
        assert until(w, "pong")


def test_last_play_wins_and_relay_rules(client):
    pid, a, b = mk(client), dev(), dev()
    with conn(client, pid, a) as wa, conn(client, pid, b) as wb:
        until(wa, "state"); until(wb, "state")
        wa.send_json({"type": "player_state", "track_id": 7, "position_ms": 1000, "playing": True, "at": 1})
        assert until(wa, "active_device")["device_id"] == a and until(wb, "active_device")["device_id"] == a
        relayed = until(wb, "player_state")
        assert relayed["device"] == a and relayed["track_id"] == 7 and relayed["position_ms"] == 1000
        assert not [m for m in quiet(wa) if m["type"] == "player_state"]  # the sender never gets its own state back
        # B starts playing: it takes over and A is told to stop
        wb.send_json({"type": "player_state", "track_id": 9, "position_ms": 0, "playing": True, "at": 2})
        assert until(wa, "active_device")["device_id"] == b
        # A (no longer active) pausing must not steer the profile
        quiet(wb)
        wa.send_json({"type": "player_state", "track_id": 7, "position_ms": 5000, "playing": False, "at": 3})
        assert not [m for m in quiet(wb) if m["type"] == "player_state"]
        # the active device's heartbeat is relayed to A
        wb.send_json({"type": "player_state", "track_id": 9, "position_ms": 15000, "playing": True, "at": 4})
        assert until(wa, "player_state", device=b, position_ms=15000)
    async def read(c):
        row = await c.fetchrow("SELECT state, active_device_id FROM playback_state WHERE profile_id = $1", pid)
        st = await c.fetchval("SELECT state FROM devices WHERE id = $1", a)
        return row["state"], row["active_device_id"], st
    async def go():
        c = await asyncpg.connect(_url(TEST_DB)); r = await read(c); await c.close(); return r
    state, active, a_state = asyncio.run(go())
    state, a_state = json.loads(state), json.loads(a_state)  # a bare asyncpg connection returns jsonb as text
    assert active == b and state["position_ms"] == 15000 and a_state["position_ms"] == 5000  # each device's own last report is kept


def test_queue_update_relayed_stored_and_restored_on_connect(client):
    pid, a, b = mk(client), dev(), dev()
    q = {"context": {"type": "playlist", "id": "1", "name": "Road trip"}, "context_tracks": [1, 2, 3], "order": [0, 1, 2], "context_pos": 1,
         "user_queue": [5], "current": {"track_id": 2, "from": "context"}, "shuffle": False, "repeat": "off", "position_ms": 0, "playing": False}
    with conn(client, pid, a) as wa, conn(client, pid, b) as wb:
        until(wa, "state"); until(wb, "state")
        wa.send_json({"type": "queue_update", "state": q})
        assert until(wb, "queue_update")["state"] == q
        assert not [m for m in quiet(wa) if m["type"] == "queue_update"]
        wb.send_json({"type": "queue_update", "state": "not an object"})  # ignored
    with conn(client, pid, dev()) as wc:
        s = until(wc, "state")  # earlier sockets leaving may broadcast `devices` first; clients dispatch by type
        assert s["state"] == q and s["active_device_id"] is None


def test_profiles_are_isolated_but_household_events_reach_everyone(client):
    p1, p2 = mk(client), mk(client)
    with conn(client, p1, dev()) as w1, conn(client, p2, dev()) as w2:
        until(w1, "state"); until(w2, "state")
        w1.send_json({"type": "queue_update", "state": {"x": 1}})
        w1.send_json({"type": "player_state", "track_id": 1, "position_ms": 0, "playing": True, "at": 1})
        assert not [m for m in quiet(w2) if m["type"] in ("queue_update", "player_state", "active_device")]
        for msg in ({"type": "track_cached", "video_id": "abcdefghijk", "track_id": 3},
                    {"type": "download_progress", "video_id": "abcdefghijk", "percent": 40}):
            client.portal.call(wsmod.publish_all, client.app.state.redis, msg)
            assert until(w1, msg["type"]) == msg and until(w2, msg["type"]) == msg


def test_unknown_profile_and_bad_device_rejected(client):
    for url in ["/ws?profile=99999999&device=abc", f"/ws?profile={mk(client)}&device=bad%20id!", "/ws?device=abc"]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(url) as w:
                w.receive_json()


def test_same_device_reconnecting_replaces_old_socket(client):
    pid, d = mk(client), dev()
    with conn(client, pid, d) as old:
        until(old, "state")
        with conn(client, pid, d) as new:
            assert until(new, "state")["devices"][0]["online"] is True
            with pytest.raises((WebSocketDisconnect, anyio.ClosedResourceError, anyio.EndOfStream)):  # which one depends on timing
                while True:
                    old.receive_json()
            new.send_json({"type": "ping"})
            assert until(new, "pong")


def test_transfer_hands_playback_to_another_online_device(client):
    pid, a, b = mk(client), dev(), dev()
    q = {"context_tracks": [1, 2], "current": {"track_id": 2, "from": "context"}, "position_ms": 0, "playing": True}
    with conn(client, pid, a, "Phone") as wa, conn(client, pid, b, "Laptop") as wb:
        until(wa, "state"); until(wb, "state")
        wa.send_json({"type": "queue_update", "state": q})
        until(wb, "queue_update")
        wa.send_json({"type": "player_state", "track_id": 2, "position_ms": 91_000, "playing": True, "at": 1})
        assert until(wa, "active_device")["device_id"] == a
        until(wb, "player_state")
        wa.send_json({"type": "transfer", "to_device": b})
        for w in (wa, wb):
            m = until(w, "active_device", device_id=b, resume=True)   # both are told; the old device pauses, the target resumes
        assert m["state"]["position_ms"] == 91_000 and m["state"]["context_tracks"] == [1, 2]   # the target gets the queue and the position
        assert [d["id"] for d in client.get("/api/devices", headers={"X-Profile-Id": str(pid)}).json() if d["active"]] == [b]


def test_transfer_to_an_unknown_or_offline_device_is_refused(client):
    pid, a, b = mk(client), dev(), dev()
    with conn(client, pid, b) as wb:
        until(wb, "state")
    with conn(client, pid, a) as wa:
        until(wa, "state")
        wa.send_json({"type": "player_state", "track_id": 1, "position_ms": 0, "playing": True, "at": 1})
        until(wa, "active_device")
        for target in (b, "nobody", None, 5):                       # b is known but offline
            wa.send_json({"type": "transfer", "to_device": target})
            assert until(wa, "error")["detail"] == "That device is not online"
        assert [d["id"] for d in client.get("/api/devices", headers={"X-Profile-Id": str(pid)}).json() if d["active"]] == [a]


def test_transfer_cannot_reach_another_profiles_device(client):
    p1, p2, a, b = mk(client), mk(client), dev(), dev()
    with conn(client, p1, a) as wa, conn(client, p2, b) as wb:
        until(wa, "state"); until(wb, "state")
        wa.send_json({"type": "transfer", "to_device": b})
        assert until(wa, "error")
        assert not [m for m in quiet(wb) if m["type"] == "active_device"]


def test_devices_endpoint(client):
    p1, p2, a, b = mk(client), mk(client), dev(), dev()
    h1 = {"X-Profile-Id": str(p1)}
    with conn(client, p1, a, "One") as wa:
        until(wa, "state")
        with conn(client, p2, b, "Two") as wb:
            until(wb, "state")
            got = client.get("/api/devices", headers=h1).json()
    assert [(d["id"], d["name"], d["online"]) for d in got] == [(a, "One", True)]       # only this profile's devices
    off = client.get("/api/devices", headers=h1).json()
    assert [(d["id"], d["online"]) for d in off] == [(a, False)] and off[0]["last_seen"]
    assert client.get("/api/devices").status_code == 401
