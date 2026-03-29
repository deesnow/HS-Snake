"""
Guild settings CRUD — async wrapper using asyncpg (PostgreSQL).
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
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM guild_settings WHERE guild_id = $1", guild_id
        )
        channels = [
            r["channel_id"] for r in await conn.fetch(
                "SELECT channel_id FROM monitored_channels WHERE guild_id = $1", guild_id
            )
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
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, admin_role_id)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET admin_role_id = EXCLUDED.admin_role_id""",
            guild_id, role_id,
        )


async def set_auto_detect(guild_id: int, enabled: bool) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, auto_detect)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET auto_detect = EXCLUDED.auto_detect""",
            guild_id, int(enabled),
        )


async def set_all_channels(guild_id: int, enabled: bool) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, all_channels)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET all_channels = EXCLUDED.all_channels""",
            guild_id, int(enabled),
        )


async def add_channel(guild_id: int, channel_id: int) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO monitored_channels (guild_id, channel_id)
               VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            guild_id, channel_id,
        )


async def remove_channel(guild_id: int, channel_id: int) -> None:
    async with get_db() as conn:
        await conn.execute(
            "DELETE FROM monitored_channels WHERE guild_id = $1 AND channel_id = $2",
            guild_id, channel_id,
        )
