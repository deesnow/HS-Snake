"""
Async PostgreSQL database layer for per-guild bot settings and leaderboard cache.

Schema
------
Update needed here
"""
import asyncio
import os
from contextlib import asynccontextmanager

import asyncpg

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
                max_size=15,
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
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id      BIGINT PRIMARY KEY,
            admin_role_id BIGINT,
            auto_detect   INTEGER NOT NULL DEFAULT 0,
            all_channels  INTEGER NOT NULL DEFAULT 0
        );

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

        -- Live upsert table: one row per (region, mode, rank), always current.
        CREATE TABLE IF NOT EXISTS ldb_current_entries (
            region         TEXT    NOT NULL,
            mode           TEXT    NOT NULL,
            season_id      INTEGER NOT NULL,
            rank           INTEGER NOT NULL,
            battletag      TEXT    NOT NULL,
            battletag_orig TEXT    NOT NULL,
            rating         INTEGER,
            updated_at     TEXT    NOT NULL,
            PRIMARY KEY (region, mode, rank)
        );

        CREATE INDEX IF NOT EXISTS idx_ldb_current_btag
            ON ldb_current_entries (region, mode, battletag);

        -- Refresh run audit log.
        CREATE TABLE IF NOT EXISTS ldb_refresh_log (
            id           SERIAL PRIMARY KEY,
            region       TEXT    NOT NULL,
            mode         TEXT    NOT NULL,
            season_id    INTEGER NOT NULL,
            legend_count INTEGER NOT NULL,
            is_full      INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT    NOT NULL
        );

        -- Raw rank observations for every registered player found during a refresh.
        CREATE TABLE IF NOT EXISTS player_rank_log (
            id          SERIAL PRIMARY KEY,
            battletag   TEXT    NOT NULL,
            region      TEXT    NOT NULL,
            mode        TEXT    NOT NULL,
            season_id   INTEGER NOT NULL,
            rank        INTEGER NOT NULL,
            rating      INTEGER,
            observed_at TEXT    NOT NULL
        );

        -- Daily best rank per registered player (upserted whenever a better rank is observed).
        CREATE TABLE IF NOT EXISTS player_daily_best (
            battletag   TEXT    NOT NULL,
            region      TEXT    NOT NULL,
            mode        TEXT    NOT NULL,
            season_id   INTEGER NOT NULL,
            date_utc    TEXT    NOT NULL,
            best_rank   INTEGER NOT NULL,
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (battletag, region, mode, season_id, date_utc)
        );

        -- Daily DPS per player per day (new for DPS/Season Score feature)
        CREATE TABLE IF NOT EXISTS player_daily_dps (
            battletag    TEXT    NOT NULL,
            region       TEXT    NOT NULL,
            mode         TEXT    NOT NULL,
            season_id    INTEGER NOT NULL,
            date_utc     TEXT    NOT NULL,
            dps          REAL    NOT NULL,
            best_rank    INTEGER NOT NULL,
            legend_count INTEGER NOT NULL,
            updated_at   TEXT    NOT NULL,
            PRIMARY KEY (battletag, region, mode, season_id, date_utc)
        );

        -- Season score per player per season (new for DPS/Season Score feature)
        CREATE TABLE IF NOT EXISTS player_season_score (
            battletag    TEXT    NOT NULL,
            region       TEXT    NOT NULL,
            mode         TEXT    NOT NULL,
            season_id    INTEGER NOT NULL,
            season_score REAL    NOT NULL,
            days_counted INTEGER NOT NULL,
            updated_at   TEXT    NOT NULL,
            PRIMARY KEY (battletag, region, mode, season_id)
        );
    """)

    # ── Normalize column types that may differ from old schema ───────────────
    # Converts any TEXT timestamp columns to TIMESTAMPTZ, and any DATE date_utc
    # columns to TEXT, so the whole codebase can use consistent Python types.
    await conn.execute("""
        DO $$
        DECLARE
            col RECORD;
        BEGIN
            -- Normalize *_at / observed_at / completed_at columns: TEXT → TIMESTAMPTZ
            FOR col IN
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type = 'text'
                  AND (column_name LIKE '%_at')
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I::TIMESTAMPTZ',
                    col.table_name, col.column_name, col.column_name
                );
            END LOOP;

            -- Normalize date_utc columns: DATE → TEXT
            FOR col IN
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND column_name = 'date_utc'
                  AND data_type = 'date'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER COLUMN date_utc TYPE TEXT USING date_utc::text',
                    col.table_name
                );
            END LOOP;
        END
        $$;
    """)

    # ── Sync SERIAL sequences (guards against out-of-sync sequences after data import) ──
    await conn.execute("""
        SELECT setval('ldb_refresh_log_id_seq',
            COALESCE((SELECT MAX(id) FROM ldb_refresh_log), 0) + 1, false);
        SELECT setval('player_rank_log_id_seq',
            COALESCE((SELECT MAX(id) FROM player_rank_log), 0) + 1, false);
    """)

    # ── Migrate from old snapshot tables (if they exist) ─────────────────────
    exists = await conn.fetchval(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'ldb_snapshots'"
    )
    if exists:
        # Migrate the most recent ready snapshot per (region, mode) into the new table.
        await conn.execute("""
            INSERT INTO ldb_current_entries
                (region, mode, season_id, rank, battletag, battletag_orig, rating, updated_at)
            SELECT
                s.region, s.mode, s.season_id,
                e.rank, e.battletag, e.battletag_orig, e.rating,
                s.fetched_at
            FROM ldb_entries e
            JOIN ldb_snapshots s ON s.id = e.snapshot_id
            WHERE s.ready = 1
              AND s.id = (
                SELECT id FROM ldb_snapshots s2
                WHERE s2.region = s.region AND s2.mode = s.mode AND s2.ready = 1
                ORDER BY s2.id DESC LIMIT 1
              )
            ON CONFLICT DO NOTHING
        """)
        # Drop the old tables now that data is migrated.
        await conn.execute("DROP TABLE IF EXISTS ldb_entries")
        await conn.execute("DROP TABLE IF EXISTS ldb_snapshots")

    # ── Migrate discord_id → battletag in player tracking tables ─────────────
    await conn.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'player_season_score' AND column_name = 'discord_id'
            ) THEN
                -- player_daily_best
                ALTER TABLE player_daily_best ADD COLUMN battletag TEXT;
                UPDATE player_daily_best pdb
                    SET battletag = LOWER(ub.battletag)
                    FROM user_battletags ub
                    WHERE ub.discord_id = pdb.discord_id AND ub.region = pdb.region;
                DELETE FROM player_daily_best WHERE battletag IS NULL;
                ALTER TABLE player_daily_best DROP CONSTRAINT player_daily_best_pkey;
                ALTER TABLE player_daily_best DROP COLUMN discord_id;
                ALTER TABLE player_daily_best
                    ADD PRIMARY KEY (battletag, region, mode, season_id, date_utc);

                -- player_daily_dps
                ALTER TABLE player_daily_dps ADD COLUMN battletag TEXT;
                UPDATE player_daily_dps pdd
                    SET battletag = LOWER(ub.battletag)
                    FROM user_battletags ub
                    WHERE ub.discord_id = pdd.discord_id AND ub.region = pdd.region;
                DELETE FROM player_daily_dps WHERE battletag IS NULL;
                ALTER TABLE player_daily_dps DROP CONSTRAINT player_daily_dps_pkey;
                ALTER TABLE player_daily_dps DROP COLUMN discord_id;
                ALTER TABLE player_daily_dps
                    ADD PRIMARY KEY (battletag, region, mode, season_id, date_utc);

                -- player_rank_log
                ALTER TABLE player_rank_log ADD COLUMN battletag TEXT;
                UPDATE player_rank_log prl
                    SET battletag = LOWER(ub.battletag)
                    FROM user_battletags ub
                    WHERE ub.discord_id = prl.discord_id AND ub.region = prl.region;
                DELETE FROM player_rank_log WHERE battletag IS NULL;
                DROP INDEX IF EXISTS idx_prl;
                ALTER TABLE player_rank_log DROP COLUMN discord_id;
                CREATE INDEX idx_prl
                    ON player_rank_log (battletag, region, mode, season_id, observed_at DESC);

                -- player_season_score
                ALTER TABLE player_season_score ADD COLUMN battletag TEXT;
                UPDATE player_season_score pss
                    SET battletag = LOWER(ub.battletag)
                    FROM user_battletags ub
                    WHERE ub.discord_id = pss.discord_id AND ub.region = pss.region;
                DELETE FROM player_season_score WHERE battletag IS NULL;
                ALTER TABLE player_season_score DROP CONSTRAINT player_season_score_pkey;
                ALTER TABLE player_season_score DROP COLUMN discord_id;
                ALTER TABLE player_season_score
                    ADD PRIMARY KEY (battletag, region, mode, season_id);
            END IF;
        END $$;
    """)

    # Create idx_prl here (not in the CREATE TABLE block) so it always runs after
    # the battletag column exists — either via migration above or on a fresh DB.
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_prl
            ON player_rank_log (battletag, region, mode, season_id, observed_at DESC);
    """)
