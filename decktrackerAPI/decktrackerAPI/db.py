"""
Async PostgreSQL database layer for RankTrackerAPI.

Owns and migrates its own tables (rank_api_tokens, rank_tracker_matches) in the
same Postgres instance/database the bot uses, independently of bot/services/db.py.
"""
import asyncio
from contextlib import asynccontextmanager

import asyncpg

from decktrackerAPI.config import settings

_pool = None
_pool_lock = asyncio.Lock()


async def init_db_pool():
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=settings.postgres_password,
                database=settings.postgres_db,
                min_size=2,
                max_size=10,
            )
            async with _pool.acquire() as conn:
                await _migrate(conn)


async def close_db_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_db():
    if _pool is None:
        await init_db_pool()
    async with _pool.acquire(timeout=10) as conn:
        yield conn


async def _migrate(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rank_api_tokens (
            id           SERIAL PRIMARY KEY,
            discord_id   TEXT NOT NULL,
            token_hash   TEXT NOT NULL UNIQUE,
            label        TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at   TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS rank_tracker_matches (
            game_id            UUID PRIMARY KEY,
            token_id           INTEGER NOT NULL REFERENCES rank_api_tokens(id),
            schema_version     INTEGER NOT NULL,
            start_time         TIMESTAMPTZ NOT NULL,
            end_time           TIMESTAMPTZ NOT NULL,
            game_mode          TEXT NOT NULL,
            format             TEXT NOT NULL,
            result             TEXT NOT NULL,
            was_conceded       BOOLEAN NOT NULL,
            player_battletag   TEXT NOT NULL,
            opponent_battletag TEXT NOT NULL,
            league_id          INTEGER NOT NULL,
            rank               INTEGER NOT NULL,
            star_level         INTEGER NOT NULL,
            stars              INTEGER NOT NULL,
            legend_rank        INTEGER NOT NULL,
            star_level_after   INTEGER NOT NULL,
            stars_after        INTEGER NOT NULL,
            legend_rank_after  INTEGER NOT NULL,
            received_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS idx_rank_tracker_matches_battletag
            ON rank_tracker_matches (player_battletag, end_time DESC);
    """)
