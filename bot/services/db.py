"""
Async SQLite database layer for per-guild bot settings.

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
    """)
    await db.commit()
