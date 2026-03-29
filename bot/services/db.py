"""
Async SQLite database layer for per-guild bot settings and leaderboard cache.

Schema
------
guild_settings
    guild_id        INTEGER PRIMARY KEY
    admin_role_id   INTEGER             -- role allowed to run /botadmin commands
    auto_detect     INTEGER DEFAULT 0   -- 1 = enabled, 0 = disabled
    all_channels    INTEGER DEFAULT 0   -- 1 = monitor every text channel

monitored_channels
    guild_id        INTEGER
    channel_id      INTEGER
    PRIMARY KEY (guild_id, channel_id)

user_battletags
    discord_id      TEXT PRIMARY KEY    -- Discord user snowflake (as text)
    battletag       TEXT NOT NULL       -- original casing e.g. "Player#1234"
    region          TEXT NOT NULL       -- EU | US | AP

ldb_current_entries  -- live upsert table; always reflects the latest API data
    region          TEXT NOT NULL
    mode            TEXT NOT NULL
    season_id       INTEGER NOT NULL    -- from API; used to detect season rollover
    rank            INTEGER NOT NULL
    battletag       TEXT NOT NULL       -- lower-cased for lookup
    battletag_orig  TEXT NOT NULL       -- original casing for display
    rating          INTEGER
    updated_at      TEXT NOT NULL       -- ISO-8601 UTC of last upsert
    PRIMARY KEY (region, mode, rank)

ldb_refresh_log  -- one row per completed refresh run; tracks legend pool size over time
    id           INTEGER PRIMARY KEY AUTOINCREMENT
    region       TEXT NOT NULL
    mode         TEXT NOT NULL
    season_id    INTEGER NOT NULL
    legend_count INTEGER NOT NULL  -- total rows in ldb_current_entries after this run
    is_full      INTEGER NOT NULL  -- 1 = full refresh (all pages), 0 = quick (top-N only)
    completed_at TEXT NOT NULL     -- ISO-8601 UTC

player_rank_log  -- every time a registered player appears in a refresh page
    id          INTEGER PRIMARY KEY AUTOINCREMENT
    discord_id  TEXT NOT NULL
    region      TEXT NOT NULL
    mode        TEXT NOT NULL
    season_id   INTEGER NOT NULL
    rank        INTEGER NOT NULL
    rating      INTEGER
    observed_at TEXT NOT NULL      -- ISO-8601 UTC

player_daily_best  -- best rank per registered player per UTC calendar day (upserted)
    discord_id  TEXT NOT NULL
    region      TEXT NOT NULL
    mode        TEXT NOT NULL
    season_id   INTEGER NOT NULL
    date_utc    TEXT NOT NULL      -- YYYY-MM-DD UTC
    best_rank   INTEGER NOT NULL
    updated_at  TEXT NOT NULL
    PRIMARY KEY (discord_id, region, mode, season_id, date_utc)
"""
import os
from contextlib import asynccontextmanager

import aiosqlite

_DB_PATH = os.getenv("DB_PATH", "data/bot.db")


@asynccontextmanager
async def get_db():
    """Async context manager that yields an open, migrated aiosqlite connection."""
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        db.row_factory = aiosqlite.Row
        await _migrate(db)
        yield db


async def _migrate(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id      INTEGER PRIMARY KEY,
            admin_role_id INTEGER,
            auto_detect   INTEGER NOT NULL DEFAULT 0,
            all_channels  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS monitored_channels (
            guild_id   INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
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
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            region       TEXT    NOT NULL,
            mode         TEXT    NOT NULL,
            season_id    INTEGER NOT NULL,
            legend_count INTEGER NOT NULL,
            is_full      INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT    NOT NULL
        );

        -- Raw rank observations for every registered player found during a refresh.
        CREATE TABLE IF NOT EXISTS player_rank_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id  TEXT    NOT NULL,
            region      TEXT    NOT NULL,
            mode        TEXT    NOT NULL,
            season_id   INTEGER NOT NULL,
            rank        INTEGER NOT NULL,
            rating      INTEGER,
            observed_at TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prl
            ON player_rank_log (discord_id, region, mode, season_id, observed_at DESC);

        -- Daily best rank per registered player (upserted whenever a better rank is observed).
        CREATE TABLE IF NOT EXISTS player_daily_best (
            discord_id  TEXT    NOT NULL,
            region      TEXT    NOT NULL,
            mode        TEXT    NOT NULL,
            season_id   INTEGER NOT NULL,
            date_utc    TEXT    NOT NULL,
            best_rank   INTEGER NOT NULL,
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (discord_id, region, mode, season_id, date_utc)
        );

        -- Daily DPS per player per day (new for DPS/Season Score feature)
        CREATE TABLE IF NOT EXISTS player_daily_dps (
            discord_id  TEXT    NOT NULL,
            region      TEXT    NOT NULL,
            mode        TEXT    NOT NULL,
            season_id   INTEGER NOT NULL,
            date_utc    TEXT    NOT NULL,
            dps         REAL    NOT NULL,
            best_rank   INTEGER NOT NULL,
            legend_count INTEGER NOT NULL,
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (discord_id, region, mode, season_id, date_utc)
        );

        -- Season score per player per season (new for DPS/Season Score feature)
        CREATE TABLE IF NOT EXISTS player_season_score (
            discord_id   TEXT    NOT NULL,
            region       TEXT    NOT NULL,
            mode         TEXT    NOT NULL,
            season_id    INTEGER NOT NULL,
            season_score REAL    NOT NULL,
            days_counted INTEGER NOT NULL,
            updated_at   TEXT    NOT NULL,
            PRIMARY KEY (discord_id, region, mode, season_id)
        );
    """)
    await db.commit()

    # ── Migrate from old snapshot tables (if they exist) ─────────────────────
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ldb_snapshots'"
    )
    if await cursor.fetchone():
        # Migrate the most recent ready snapshot per (region, mode) into the new table.
        await db.execute("""
            INSERT OR IGNORE INTO ldb_current_entries
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
        """)
        # Drop the old tables now that data is migrated.
        await db.executescript("""
            DROP TABLE IF EXISTS ldb_entries;
            DROP TABLE IF EXISTS ldb_snapshots;
        """)
        await db.commit()
