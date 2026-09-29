"""
Guild settings CRUD — async wrapper using asyncpg (PostgreSQL).

load() is called for every guild message (auto-detect), so results are cached
in-process. Only this module writes these tables, and every write invalidates
the guild's entry, so the cache needs no TTL.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from bot.services.db import get_db

# How auto-detected deck codes are shown.
DECK_DISPLAY_LIST = "list"    # simple card list, same as /deck
DECK_DISPLAY_IMAGE = "image"  # rendered deck image, same as /deckimage
DECK_DISPLAYS = (DECK_DISPLAY_LIST, DECK_DISPLAY_IMAGE)


@dataclass
class GuildSettings:
    guild_id: int
    admin_role_id: Optional[int] = None
    auto_detect: bool = False
    all_channels: bool = False
    deck_display: str = DECK_DISPLAY_LIST
    monitored_channels: list[int] = field(default_factory=list)


_cache: dict[int, GuildSettings] = {}
# Bumped on every write; a load() that raced with a write doesn't cache its
# (possibly stale) result.
_generation: dict[int, int] = {}


def _invalidate(guild_id: int) -> None:
    _generation[guild_id] = _generation.get(guild_id, 0) + 1
    _cache.pop(guild_id, None)


async def load(guild_id: int) -> GuildSettings:
    cached = _cache.get(guild_id)
    if cached is not None:
        return cached

    generation = _generation.get(guild_id, 0)
    settings = await _load_from_db(guild_id)
    if _generation.get(guild_id, 0) == generation:
        _cache[guild_id] = settings
    return settings


async def _load_from_db(guild_id: int) -> GuildSettings:
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
        deck_display=row["deck_display"],
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
    _invalidate(guild_id)


async def set_auto_detect(guild_id: int, enabled: bool) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, auto_detect)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET auto_detect = EXCLUDED.auto_detect""",
            guild_id, int(enabled),
        )
    _invalidate(guild_id)


async def set_all_channels(guild_id: int, enabled: bool) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, all_channels)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET all_channels = EXCLUDED.all_channels""",
            guild_id, int(enabled),
        )
    _invalidate(guild_id)


async def set_deck_display(guild_id: int, deck_display: str) -> None:
    if deck_display not in DECK_DISPLAYS:
        raise ValueError(f"deck_display must be one of {DECK_DISPLAYS}, got {deck_display!r}")
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, deck_display)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET deck_display = EXCLUDED.deck_display""",
            guild_id, deck_display,
        )
    _invalidate(guild_id)


async def add_channel(guild_id: int, channel_id: int) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO monitored_channels (guild_id, channel_id)
               VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            guild_id, channel_id,
        )
    _invalidate(guild_id)


async def remove_channel(guild_id: int, channel_id: int) -> None:
    async with get_db() as conn:
        await conn.execute(
            "DELETE FROM monitored_channels WHERE guild_id = $1 AND channel_id = $2",
            guild_id, channel_id,
        )
    _invalidate(guild_id)
