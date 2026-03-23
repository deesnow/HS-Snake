"""
hs-snake bot — entry point.

Loads all command cogs and connects to Discord.
"""
import asyncio
import logging

import discord
from discord.ext import commands

from bot.config import settings

__version__ = "0.2.2"

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


class HsSnakeBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # privileged intent — must also be enabled in the Discord Developer Portal
        super().__init__(command_prefix=settings.command_prefix, intents=intents)

    async def setup_hook(self) -> None:
        # Load command cogs
        await self.load_extension("bot.commands.deck_commands")
        await self.load_extension("bot.commands.card_commands")
        await self.load_extension("bot.commands.admin_commands")
        await self.load_extension("bot.commands.auto_detect")

        # Sync slash commands (guild-scoped during dev, global in prod)
        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %s", settings.discord_guild_id)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally")

    async def on_ready(self) -> None:
        log.info("HS-Snake v%s — logged in as %s (id=%s)", __version__, self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"Hearthstone Assistant v{__version__} - by DeeSnow",
            )
        )


async def main() -> None:
    async with HsSnakeBot() as bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
