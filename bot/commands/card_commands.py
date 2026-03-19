"""
Card lookup slash command: /card.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.hs_json_client import HSJsonClient

log = logging.getLogger(__name__)

RARITY_COLOURS = {
    "FREE": 0xAAAAAA,
    "COMMON": 0xFFFFFF,
    "RARE": 0x0070DD,
    "EPIC": 0xA335EE,
    "LEGENDARY": 0xFF8000,
}


class CardCommands(commands.Cog):
    """Commands for individual Hearthstone card lookups."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.hs_client = HSJsonClient()

    @app_commands.command(name="card", description="Look up a Hearthstone card by name.")
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

            colour = RARITY_COLOURS.get(card.rarity.upper(), 0xFFFFFF)
            embed = discord.Embed(title=card.name, colour=colour)
            embed.add_field(name="Class", value=card.card_class or "Neutral", inline=True)
            embed.add_field(name="Type", value=card.card_type.capitalize(), inline=True)
            embed.add_field(name="Mana Cost", value=str(card.cost), inline=True)
            embed.add_field(name="Rarity", value=card.rarity.capitalize(), inline=True)
            embed.add_field(name="Set", value=card.card_set or "—", inline=True)
            if card.attack is not None:
                embed.add_field(name="Attack", value=str(card.attack), inline=True)
            if card.health is not None:
                embed.add_field(name="Health", value=str(card.health), inline=True)
            if card.durability is not None:
                embed.add_field(name="Durability", value=str(card.durability), inline=True)
            if card.text:
                embed.add_field(name="Text", value=card.text, inline=False)

            # Thumbnail via HSJson art endpoint
            embed.set_thumbnail(
                url=f"https://art.hearthstonejson.com/v1/tiles/{card.dbf_id}.png"
            )
            await interaction.followup.send(embed=embed)

        except Exception:
            log.exception("Unexpected error in /card")
            await interaction.followup.send("❌ Something went wrong. Please try again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CardCommands(bot))
