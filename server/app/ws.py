"""WebSocket hub (planning.md §8, D34, D35, D39).

All fan-out goes through Redis pub/sub: `ws:all` for household-wide events (the worker publishes track_cached etc.) and
`ws:profile:<id>` for player and queue state. The API subscribes once and forwards to its local sockets, which is why the API
runs as a single process. A `_x` field in a published message names a device to skip (the sender); it is removed before delivery.
"""
import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketDisconnected

from . import config
from .routes.profiles import current_profile

log = logging.getLogger("resonance.ws")
router = APIRouter()
MAX_MESSAGE = 256 * 1024
DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def publish_all(redis, msg: dict) -> None:
    """Household-wide events: track_cached, download_progress, download_failed."""
    await redis.publish("ws:all", json.dumps(msg))


async def publish_profile(redis, profile_id: int, msg: dict, exclude: str | None = None) -> None:
    await redis.publish(f"ws:profile:{profile_id}", json.dumps({**msg, "_x": exclude} if exclude else msg))


class Hub:
    def __init__(self):
        self.sockets: dict[int, dict[str, WebSocket]] = {}  # profile id -> device id -> socket
        self.pubsub = None
        self.task: asyncio.Task | None = None
        self.background: set[asyncio.Task] = set()  # keeps detached cleanup tasks alive until they finish

    def forget(self, profile_id: int, device_id: str):
        del self.sockets[profile_id][device_id]
        if not self.sockets[profile_id]:
            del self.sockets[profile_id]

    async def start(self, redis):
        self.pubsub = redis.pubsub()
        await self.pubsub.psubscribe("ws:*")  # subscribed before the first socket can connect
        self.task = asyncio.create_task(self._listen())

    async def stop(self):
        if self.task:
            self.task.cancel()
        if self.pubsub:
            await self.pubsub.aclose()

    def online(self, profile_id: int) -> set[str]:
        return set(self.sockets.get(profile_id, {}))

    async def _send(self, sockets, text: str):
        async def one(w):
            try:
                await w.send_text(text)
            except Exception:  # a dead socket is cleaned up by its own receive loop
                pass
        await asyncio.gather(*(one(w) for w in sockets))

    async def _listen(self):
        async for m in self.pubsub.listen():
            if m["type"] != "pmessage":
                continue
            try:
                msg = json.loads(m["data"])
                skip = msg.pop("_x", None)
                text = json.dumps(msg)
                chan = m["channel"]
                if chan == "ws:all":
                    targets = [w for devs in self.sockets.values() for w in devs.values()]
                elif chan.startswith("ws:profile:"):
                    targets = [w for d, w in self.sockets.get(int(chan.rsplit(":", 1)[1]), {}).items() if d != skip]
                else:
                    continue
                await self._send(targets, text)
            except Exception:
                log.exception("bad pub/sub message on %s", m.get("channel"))


async def devices_list(app, profile_id: int) -> list[dict]:
    rows = await app.state.pool.fetch("SELECT id, name, last_seen FROM devices WHERE profile_id = $1 ORDER BY last_seen DESC", profile_id)
    active = await app.state.pool.fetchval("SELECT active_device_id FROM playback_state WHERE profile_id = $1", profile_id)
    online = app.state.hub.online(profile_id)
    return [{"id": r["id"], "name": r["name"], "online": r["id"] in online, "active": r["id"] == active, "last_seen": r["last_seen"].isoformat()} for r in rows]


async def broadcast_devices(app, profile_id: int):
    await publish_profile(app.state.redis, profile_id, {"type": "devices", "list": await devices_list(app, profile_id)})


async def set_active(app, profile_id: int, device_id: str, resume: bool = False):
    await app.state.pool.execute(
        "INSERT INTO playback_state (profile_id, state, active_device_id) VALUES ($1, '{}', $2) "
        "ON CONFLICT (profile_id) DO UPDATE SET active_device_id = $2, updated_at = now()", profile_id, device_id)
    msg = {"type": "active_device", "device_id": device_id, "resume": resume}
    if resume:  # a transfer carries the stored queue and position so the target can continue at once
        msg["state"] = await app.state.pool.fetchval("SELECT state FROM playback_state WHERE profile_id = $1", profile_id) or None
    await publish_profile(app.state.redis, profile_id, msg)
    await broadcast_devices(app, profile_id)


AUTOPLAY_CONTEXTS = {"playlist", "album", "artist", "liked"}  # a `radio` context extends itself; these start a radio when they run out (D51)
AUTOPLAY_GATE_S = 600


async def maybe_autoplay(app, profile_id: int, state: dict) -> None:
    """D51: when the last item of a list is playing, ask the worker for more songs. Runs on every stored queue_update / player_state,
    so most calls must return at once. arq's job id dedupes while a job is queued or running; the Redis gate covers the time after it."""
    try:
        if not config.autoplay_methods():
            return
        order, tracks, cur, ctx = state.get("order"), state.get("context_tracks"), state.get("current") or {}, state.get("context") or {}
        if not (state.get("playing") is True and isinstance(order, list) and isinstance(tracks, list) and order and cur.get("from") == "context"
                and state.get("context_pos") == len(order) - 1 and isinstance(cur.get("track_id"), int)):
            return
        ctype, cid = ctx.get("type"), str(ctx.get("id") or "")
        if ctype != "radio" and ctype not in AUTOPLAY_CONTEXTS:  # search / queue contexts never autoplay
            return
        pool = app.state.pool
        prefs = await pool.fetchval("SELECT prefs FROM profiles WHERE id = $1", profile_id) or {}
        if prefs.get("autoplay") is False:
            return
        job_id = f"ap:{profile_id}:{ctype}:{cid}:{len(tracks)}"
        if not await app.state.redis.set(f"apf:{job_id}", "1", nx=True, ex=AUTOPLAY_GATE_S):
            return
        recent = [r["track_id"] for r in await pool.fetch(
            "SELECT track_id FROM play_history WHERE profile_id = $1 GROUP BY track_id ORDER BY max(played_at) DESC LIMIT 50", profile_id)]
        exclude = sorted({t for t in tracks if isinstance(t, int)} | set(recent))
        await app.state.arq.enqueue_job("autoplay_extend", profile_id, f"{ctype}:{cid}", len(tracks), cur["track_id"], exclude,
                                        method=config.autoplay_method(prefs), _job_id=job_id)
    except Exception:
        log.exception("autoplay trigger failed")  # never let this break the socket loop


async def on_player_state(app, profile_id: int, device_id: str, msg: dict):
    st = {k: msg.get(k) for k in ("track_id", "position_ms", "playing", "at")}
    await app.state.pool.execute("UPDATE devices SET state = $2, last_seen = now() WHERE id = $1", device_id, st)
    active = await app.state.pool.fetchval("SELECT active_device_id FROM playback_state WHERE profile_id = $1", profile_id)
    if st["playing"] and active != device_id:  # last play wins (D35)
        await set_active(app, profile_id, device_id)
        active = device_id
    if active != device_id:
        return  # a paused, non-active device doesn't steer the profile
    merged = await app.state.pool.fetchval(
        "INSERT INTO playback_state (profile_id, state, active_device_id) VALUES ($1, $2, $3) "
        "ON CONFLICT (profile_id) DO UPDATE SET state = playback_state.state || $2::jsonb, updated_at = now() RETURNING state",
        profile_id, {"position_ms": st["position_ms"], "playing": st["playing"]}, device_id)
    await publish_profile(app.state.redis, profile_id, {"type": "player_state", **st, "device": device_id}, exclude=device_id)
    await maybe_autoplay(app, profile_id, merged)


async def on_transfer(app, profile_id: int, ws: WebSocket, msg: dict):
    """Hand playback to another of this profile's online devices (M4.1-B). The target resumes; every other device pauses."""
    to = msg.get("to_device")
    if not isinstance(to, str) or to not in app.state.hub.online(profile_id):
        await ws.send_json({"type": "error", "detail": "That device is not online"})
        return
    await set_active(app, profile_id, to, resume=True)


async def on_queue_update(app, profile_id: int, device_id: str, msg: dict):
    state = msg.get("state")
    if not isinstance(state, dict):
        return
    await app.state.pool.execute(
        "INSERT INTO playback_state (profile_id, state) VALUES ($1, $2) "
        "ON CONFLICT (profile_id) DO UPDATE SET state = $2, updated_at = now()", profile_id, state)
    await publish_profile(app.state.redis, profile_id, {"type": "queue_update", "state": state}, exclude=device_id)
    await maybe_autoplay(app, profile_id, state)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, profile: int, device: str, name: str = "Browser"):
    app = ws.app
    if not DEVICE_RE.match(device) or not await app.state.pool.fetchval("SELECT 1 FROM profiles WHERE id = $1", profile):
        await ws.close()  # rejected at the handshake (HTTP 403): unknown profile or malformed device id (D10)
        return
    await ws.accept()
    hub: Hub = app.state.hub
    name = name.strip()[:80] or "Browser"
    await app.state.pool.execute(
        "INSERT INTO devices (id, profile_id, name) VALUES ($1, $2, $3) "
        "ON CONFLICT (id) DO UPDATE SET profile_id = $2, name = $3, last_seen = now()", device, profile, name)
    old = hub.sockets.setdefault(profile, {}).get(device)
    hub.sockets[profile][device] = ws
    if old:  # the same device reconnected before its old socket noticed it was gone
        await old.close(code=4000)
    try:
        row = await app.state.pool.fetchrow("SELECT state, active_device_id FROM playback_state WHERE profile_id = $1", profile)
        await ws.send_json({"type": "state", "state": (row["state"] or None) if row else None,
                            "active_device_id": row["active_device_id"] if row else None, "devices": await devices_list(app, profile)})
        await broadcast_devices(app, profile)
        while True:
            text = await ws.receive_text()
            if len(text) > MAX_MESSAGE:
                await ws.close(code=1009)
                break
            try:
                msg = json.loads(text)
                kind = msg.get("type")
            except (ValueError, AttributeError):
                continue
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "player_state":
                await on_player_state(app, profile, device, msg)
            elif kind == "queue_update":
                await on_queue_update(app, profile, device, msg)
            elif kind == "transfer":
                await on_transfer(app, profile, ws, msg)  # unknown message types are ignored
    except (WebSocketDisconnect, WebSocketDisconnected):  # the second: this socket was closed from elsewhere (device reconnected)
        pass
    finally:
        hub.sockets.get(profile, {}).get(device) is ws and hub.forget(profile, device)  # synchronous: nothing can cancel it
        task = asyncio.ensure_future(_left(app, profile, device))  # a detached task, so cancelling this handler can't cut it short
        hub.background.add(task)
        task.add_done_callback(hub.background.discard)


async def _left(app, profile_id: int, device_id: str):
    await app.state.pool.execute("UPDATE devices SET last_seen = now() WHERE id = $1", device_id)
    await broadcast_devices(app, profile_id)


@router.get("/api/devices")
async def list_devices(request: Request, p=Depends(current_profile)):
    """The profile's devices: `[{id, name, online, active, last_seen}]`."""
    return await devices_list(request.app, p["id"])
