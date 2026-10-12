"""asyncpg pool and the numbered-SQL migration runner (D4)."""
import json
from pathlib import Path

import asyncpg

from . import config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LOCK_KEY = 727_001  # pg_advisory_lock key shared by api and worker


async def _init(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10, init=_init)


async def migrate(pool: asyncpg.Pool) -> list[str]:
    """Apply pending migrations once, even when api and worker start together. Returns applied versions."""
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations"
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.stem in done:
                    continue
                async with conn.transaction():
                    await conn.execute(f.read_text())
                    await conn.execute("INSERT INTO schema_migrations(version) VALUES ($1)", f.stem)
                applied.append(f.stem)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    return applied
