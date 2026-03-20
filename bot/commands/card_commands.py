"""
Card lookup slash command: /card.
"""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.hs_json_client import HSJsonClient

log = logging.getLogger(__name__)


class CardCommands(commands.Cog):
    """Commands for individual Hearthstone card lookups."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.hs_client = HSJsonClient()

    @app_commands.command(name="card", description="Show the card picture for a Hearthstone card.")
    @app_commands.describe(name="Card name to search for")
    async def card(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()
        try:
            card = await self.hs_client.find_card_by_name(name.strip())
            if card is None:
                await interaction.followup.send(
                    f"❌ No card found matching **{name}**.", ephemeral=True
                )
                return

            image_bytes = await self.hs_client.get_card_image_bytes(card.card_id, card.dbf_id)
            file = discord.File(fp=io.BytesIO(image_bytes), filename=f"{card.card_id}.png")
            await interaction.followup.send(file=file)

        except Exception:
            log.exception("Unexpected error in /card")
            await interaction.followup.send("❌ Something went wrong. Please try again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CardCommands(bot))
