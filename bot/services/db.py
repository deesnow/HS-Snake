"""
Async PostgreSQL database layer for per-guild bot settings and leaderboard cache.

Schema (bot-owned; rank_api_tokens and rank_tracker_matches belong to
decktrackerAPI and share this database)
------
guild_settings, monitored_channels  per-guild bot configuration
user_battletags                     registered BattleTag per (discord_id, region)
ldb_current_entries                 live leaderboard, one row per (region, mode, rank)
ldb_seasons                         one row per (region, mode, season): first/last refresh
ldb_refresh_log                     per-run refresh audit log, pruned to 14 days
player_rank_log                     rank observations of registered players
player_daily_dps                    best rank and DPS per player per UTC day
player_season_score                 cached season score, derived from player_daily_dps
schema_migrations                   one-off migrations that have already run

_migrate() runs on every startup: idempotent CREATE ... IF NOT EXISTS for the
current schema, then each step in _ONE_OFF_MIGRATIONS not yet recorded in
schema_migrations.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import asyncpg

log = logging.getLogger(__name__)

_DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
_DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
_DB_USER = os.getenv("POSTGRES_USER")
_DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
_DB_NAME = os.getenv("POSTGRES_DB")

_pool = None
_pool_lock = asyncio.Lock()

async def init_db_pool():
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                host=_DB_HOST,
                port=_DB_PORT,
                user=_DB_USER,
                password=_DB_PASSWORD,
                database=_DB_NAME,
                min_size=2,
                # 6 concurrent leaderboard refreshes hold one connection each
                # for their whole run; the rest serve commands. Postgres's
                # default max_connections (100) covers this plus rank-api's 10.
                max_size=20,
            )
            async with _pool.acquire() as conn:
                await _migrate(conn)

@asynccontextmanager
async def get_db():
    if _pool is None:
        await init_db_pool()
    async with _pool.acquire(timeout=10) as conn:
        yield conn


async def _migrate(conn: asyncpg.Connection) -> None:
    # Serialise concurrent startups (e.g. a restart overlapping the old process).
    await conn.execute("SELECT pg_advisory_lock(hashtext('hs-snake bot migrations'))")
    try:
        await _create_schema(conn)
        for name, step in _ONE_OFF_MIGRATIONS:
            await _run_once(conn, name, step)
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtext('hs-snake bot migrations'))")


async def _create_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id      BIGINT PRIMARY KEY,
            admin_role_id BIGINT,
            auto_detect   INTEGER NOT NULL DEFAULT 0,
            all_channels  INTEGER NOT NULL DEFAULT 0,
            -- How auto-detected deck codes are shown: 'list' (/deck) or 'image' (/deckimage).
            deck_display  TEXT    NOT NULL DEFAULT 'list'
        );
        ALTER TABLE guild_settings
            ADD COLUMN IF NOT EXISTS deck_display TEXT NOT NULL DEFAULT 'list';

        CREATE TABLE IF NOT EXISTS monitored_channels (
            guild_id   BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            PRIMARY KEY (guild_id, channel_id)
        );

        CREATE TABLE IF NOT EXISTS user_battletags (
            discord_id  TEXT NOT NULL,
            region      TEXT NOT NULL,
            battletag   TEXT NOT NULL,
            PRIMARY KEY (discord_id, region)
        );

        -- Bearer tokens for RankTrackerAPI (decktrackerAPI/), shared with the bot via
        -- the same Postgres instance. Authoritative schema lives in
        -- decktrackerAPI/decktrackerAPI/db.py — mirrored here defensively so /hdttoken
        -- works even if this table hasn't been created by that service yet.
        CREATE TABLE IF NOT EXISTS rank_api_tokens (
            id           SERIAL PRIMARY KEY,
            discord_id   TEXT NOT NULL,
            token_hash   TEXT NOT NULL UNIQUE,
            label        TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at   TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ
        );

        -- Live upsert table: one row per (region, mode, rank), always current.
        CREATE TABLE IF NOT EXISTS ldb_current_entries (
            region         TEXT        NOT NULL,
            mode           TEXT        NOT NULL,
            season_id      INTEGER     NOT NULL,
            rank           INTEGER     NOT NULL,
            battletag      TEXT        NOT NULL,
            battletag_orig TEXT        NOT NULL,
            rating         INTEGER,
            updated_at     TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (region, mode, rank)
        );

        CREATE INDEX IF NOT EXISTS idx_ldb_current_btag
            ON ldb_current_entries (region, mode, battletag);

        -- One row per season ever refreshed: which month a season belongs to
        -- (first_refresh_at) and whether a refresh ran this month (last_refresh_at).
        CREATE TABLE IF NOT EXISTS ldb_seasons (
            region           TEXT        NOT NULL,
            mode             TEXT        NOT NULL,
            season_id        INTEGER     NOT NULL,
            first_refresh_at TIMESTAMPTZ NOT NULL,
            last_refresh_at  TIMESTAMPTZ NOT NULL,
            legend_count     INTEGER     NOT NULL,
            PRIMARY KEY (region, mode, season_id)
        );

        -- Refresh run audit log, for troubleshooting only (nothing reads it).
        -- Pruned to 14 days by leaderboard_cache.prune_refresh_log.
        CREATE TABLE IF NOT EXISTS ldb_refresh_log (
            id           SERIAL      PRIMARY KEY,
            region       TEXT        NOT NULL,
            mode         TEXT        NOT NULL,
            season_id    INTEGER     NOT NULL,
            legend_count INTEGER     NOT NULL,
            is_full      INTEGER     NOT NULL DEFAULT 0,
            completed_at TIMESTAMPTZ NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ldb_refresh_log_completed
            ON ldb_refresh_log (completed_at);

        -- Rank observations for every registered player found during a refresh.
        -- No primary key: two leaderboard players with the same name can both
        -- match one registered battletag in the same refresh (same observed_at).
        CREATE TABLE IF NOT EXISTS player_rank_log (
            battletag   TEXT        NOT NULL,
            region      TEXT        NOT NULL,
            mode        TEXT        NOT NULL,
            season_id   INTEGER     NOT NULL,
            rank        INTEGER     NOT NULL,
            rating      INTEGER,
            observed_at TIMESTAMPTZ NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prl
            ON player_rank_log (battletag, region, mode, season_id, observed_at DESC);

        -- Per-region/mode/season scans: /glb's per-day rank lookup and the
        -- refresh's rank-log dedupe seed.
        CREATE INDEX IF NOT EXISTS idx_prl_region_season_time
            ON player_rank_log (region, mode, season_id, observed_at DESC);

        -- Daily DPS per player per day (new for DPS/Season Score feature)
        CREATE TABLE IF NOT EXISTS player_daily_dps (
            battletag    TEXT        NOT NULL,
            region       TEXT        NOT NULL,
            mode         TEXT        NOT NULL,
            season_id    INTEGER     NOT NULL,
            date_utc     TEXT        NOT NULL,
            dps          REAL        NOT NULL,
            best_rank    INTEGER     NOT NULL,
            legend_count INTEGER     NOT NULL,
            updated_at   TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (battletag, region, mode, season_id, date_utc)
        );

        -- Season score per player per season (new for DPS/Season Score feature)
        CREATE TABLE IF NOT EXISTS player_season_score (
            battletag    TEXT        NOT NULL,
            region       TEXT        NOT NULL,
            mode         TEXT        NOT NULL,
            season_id    INTEGER     NOT NULL,
            season_score REAL        NOT NULL,
            days_counted INTEGER     NOT NULL,
            updated_at   TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (battletag, region, mode, season_id)
        );

        -- /glb ordering.
        CREATE INDEX IF NOT EXISTS idx_pss_region_season_score
            ON player_season_score (region, mode, season_id, season_score DESC);

        -- The refresh's per-region load and /glb's LOWER(battletag) join.
        CREATE INDEX IF NOT EXISTS idx_ub_region_btag
            ON user_battletags (region, LOWER(battletag));
    """)

    # Guards against an out-of-sync sequence after a data import.
    await conn.execute("""
        SELECT setval('ldb_refresh_log_id_seq',
            COALESCE((SELECT MAX(id) FROM ldb_refresh_log), 0) + 1, false);
    """)


# ── One-off migrations ────────────────────────────────────────────────────────
# Each runs once, in a transaction together with its schema_migrations row, so a
# failed step is retried on the next startup. Append new steps; never reorder
# or rename existing ones. "vacuum" tables get VACUUM FULL after the commit (it
# can't run inside a transaction) to give the freed space back to the OS.

_ONE_OFF_MIGRATIONS: list[tuple[str, dict]] = [
    ("2026-09-29_drop_player_daily_best", {
        # Written by every refresh but never read; identical to player_daily_dps.best_rank.
        "sql": "DROP TABLE IF EXISTS player_daily_best",
    }),
    ("2026-09-29_backfill_ldb_seasons", {
        # Must run before the refresh-log prune below, which deletes the history.
        "sql": """
            INSERT INTO ldb_seasons
                (region, mode, season_id, first_refresh_at, last_refresh_at, legend_count)
            SELECT DISTINCT ON (region, mode, season_id)
                   region, mode, season_id,
                   MIN(completed_at) OVER (PARTITION BY region, mode, season_id),
                   completed_at,
                   legend_count
            FROM ldb_refresh_log
            ORDER BY region, mode, season_id, completed_at DESC
            ON CONFLICT (region, mode, season_id) DO NOTHING
        """,
    }),
    ("2026-09-29_prune_ldb_refresh_log", {
        "sql": "DELETE FROM ldb_refresh_log WHERE completed_at < now() - interval '14 days'",
        "vacuum": ["ldb_refresh_log"],
    }),
    ("2026-09-29_compact_player_rank_log", {
        # Deletes only rows strictly inside a same-day run of identical
        # (rank, rating) readings: the previous and next reading for that
        # player/region/mode/season are on the same UTC day with the same values.
        # Each day keeps the first and last row of every run, so per-day
        # open/close/best/worst, the latest reading and every distinct value are
        # unchanged. Rows from yesterday and today are never touched, so the
        # Today chart (one dot per row) is unaffected. Then drops the unused
        # surrogate id and its primary-key index.
        "sql": """
            DELETE FROM player_rank_log p
            USING (
                SELECT row_ctid FROM (
                    SELECT ctid AS row_ctid, observed_at, rank, rating,
                           lag(rank)         OVER w AS prev_rank,
                           lag(rating)       OVER w AS prev_rating,
                           lag(observed_at)  OVER w AS prev_at,
                           lead(rank)        OVER w AS next_rank,
                           lead(rating)      OVER w AS next_rating,
                           lead(observed_at) OVER w AS next_at
                    FROM player_rank_log
                    WINDOW w AS (PARTITION BY battletag, region, mode, season_id ORDER BY observed_at)
                ) t
                WHERE observed_at < (date_trunc('day', now() AT TIME ZONE 'UTC') - interval '1 day') AT TIME ZONE 'UTC'
                  AND prev_at IS NOT NULL AND next_at IS NOT NULL
                  AND (prev_at AT TIME ZONE 'UTC')::date = (observed_at AT TIME ZONE 'UTC')::date
                  AND (next_at AT TIME ZONE 'UTC')::date = (observed_at AT TIME ZONE 'UTC')::date
                  AND rank = prev_rank AND rank = next_rank
                  AND rating IS NOT DISTINCT FROM prev_rating
                  AND rating IS NOT DISTINCT FROM next_rating
            ) d
            WHERE p.ctid = d.row_ctid;

            ALTER TABLE player_rank_log DROP COLUMN IF EXISTS id;
            ALTER TABLE player_rank_log ALTER COLUMN battletag SET NOT NULL;
        """,
        "vacuum": ["player_rank_log"],
    }),
]


async def _run_once(conn: asyncpg.Connection, name: str, step: dict) -> None:
    done = await conn.fetchval("SELECT 1 FROM schema_migrations WHERE name = $1", name)
    if done:
        return
    log.info("Running one-off migration %s", name)
    async with conn.transaction():
        status = await conn.execute(step["sql"])
        await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", name)
    log.info("One-off migration %s done (%s)", name, status)
    for table in step.get("vacuum", []):
        try:
            await conn.execute(f"VACUUM FULL {table}")
        except Exception:
            log.warning("VACUUM FULL %s after %s failed", table, name, exc_info=True)
