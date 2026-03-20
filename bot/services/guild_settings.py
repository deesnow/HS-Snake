"""
Guild settings CRUD — thin async wrapper around the SQLite schema.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from bot.services.db import get_db


@dataclass
class GuildSettings:
    guild_id: int
    admin_role_id: Optional[int] = None
    auto_detect: bool = False
    all_channels: bool = False
    monitored_channels: list[int] = field(default_factory=list)


async def load(guild_id: int) -> GuildSettings:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )).fetchone()

        channels = [
            r["channel_id"] for r in await (await db.execute(
                "SELECT channel_id FROM monitored_channels WHERE guild_id = ?", (guild_id,)
            )).fetchall()
        ]

    if row is None:
        return GuildSettings(guild_id=guild_id, monitored_channels=channels)

    return GuildSettings(
        guild_id=guild_id,
        admin_role_id=row["admin_role_id"],
        auto_detect=bool(row["auto_detect"]),
        all_channels=bool(row["all_channels"]),
        monitored_channels=channels,
    )


async def set_admin_role(guild_id: int, role_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, admin_role_id)
               VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET admin_role_id = excluded.admin_role_id""",
            (guild_id, role_id),
        )
        await db.commit()


async def set_auto_detect(guild_id: int, enabled: bool) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, auto_detect)
               VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET auto_detect = excluded.auto_detect""",
            (guild_id, int(enabled)),
        )
        await db.commit()


async def set_all_channels(guild_id: int, enabled: bool) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, all_channels)
               VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET all_channels = excluded.all_channels""",
            (guild_id, int(enabled)),
        )
        await db.commit()


async def add_channel(guild_id: int, channel_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO monitored_channels (guild_id, channel_id) VALUES (?, ?)""",
            (guild_id, channel_id),
        )
        await db.commit()


async def remove_channel(guild_id: int, channel_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "DELETE FROM monitored_channels WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        await db.commit()
