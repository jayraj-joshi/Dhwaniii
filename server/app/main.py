import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import redis.asyncio as aioredis
import yt_dlp
from arq import create_pool as create_arq_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, db, ws, youtube
from .routes import admin, discovery, home, imports, library, playlists, profiles, search, stats, stream, tracks


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await db.create_pool()
    await db.migrate(app.state.pool)
    app.state.redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    app.state.arq = await create_arq_pool(RedisSettings.from_dsn(config.REDIS_URL))
    app.state.hub = ws.Hub()
    await app.state.hub.start(app.state.redis)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(15, read=60), follow_redirects=True)
    warm = asyncio.create_task(youtube.warm_up())  # not awaited: the API serves immediately
    yield
    warm.cancel()
    await app.state.http.aclose()
    await app.state.hub.stop()
    await app.state.arq.aclose()
    await app.state.redis.aclose()
    await app.state.pool.close()


app = FastAPI(title="Resonance", lifespan=lifespan)
if config.CORS_ORIGINS:  # no credentials are used (D10), so "*" headers are safe; Range is what <audio> sends, Content-Range what it reads back
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"], max_age=86400)
app.include_router(profiles.router)
app.include_router(search.router)
app.include_router(tracks.router)
app.include_router(library.router)
app.include_router(playlists.router)
app.include_router(discovery.router)
app.include_router(home.router)
app.include_router(imports.router)
app.include_router(admin.router)
app.include_router(stats.router)
app.include_router(stream.router)
app.include_router(ws.router)

# a plain HTML/JS console for trying every endpoint by hand; same origin as the API, so no CORS
app.mount("/console", StaticFiles(directory=Path(__file__).parent / "static" / "console", html=True), name="console")


@app.get("/api/health")
async def health():
    await app.state.pool.fetchval("SELECT 1")
    await app.state.redis.ping()
    return {"ok": True, "yt_dlp_version": yt_dlp.version.__version__}
