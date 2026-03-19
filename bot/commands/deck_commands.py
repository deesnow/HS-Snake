"""
Deck-related slash commands: /deck and /deckimage.
"""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.deck_decoder import DeckDecoder
from bot.services.hs_json_client import HSJsonClient
from bot.services.image_generator import ImageGenerator

log = logging.getLogger(__name__)

# Rarity colour map for embed side-stripe and icons
RARITY_ICON = {
    "FREE": "⚪",
    "COMMON": "⚪",
    "RARE": "🔵",
    "EPIC": "🟣",
    "LEGENDARY": "🟠",
}

FORMAT_LABELS = {
    1: "Wild",
    2: "Standard",
    3: "Classic",
    4: "Twist",
}


def _dust_cost(rarity: str, count: int) -> int:
    costs = {"FREE": 0, "COMMON": 40, "RARE": 100, "EPIC": 400, "LEGENDARY": 1600}
    per_copy = costs.get(rarity, 0)
    # Only the first legendary copy counts; second copy is never crafted in HS
    if rarity == "LEGENDARY":
        return per_copy
    return per_copy * count


def build_deck_embed(deck: "DeckInfo") -> discord.Embed:  # type: ignore[name-defined]
    """Return a nicely formatted Discord embed for a deck."""
    title = f"🐍 {deck.hero_class} — {deck.format_label}"
    embed = discord.Embed(title=title, colour=0x1A1A2E)

    type_order = ["MINION", "SPELL", "WEAPON", "HERO", "LOCATION"]
    grouped: dict[str, list] = {t: [] for t in type_order}
    grouped["OTHER"] = []

    for entry in deck.cards:
        card_type = entry.card.card_type.upper()
        grouped.get(card_type, grouped["OTHER"]).append(entry)

    total_dust = 0
    for type_key in type_order + ["OTHER"]:
        entries = grouped[type_key]
        if not entries:
            continue
        label = type_key.capitalize() + "s"
        lines = []
        for entry in sorted(entries, key=lambda e: (e.card.cost, e.card.name)):
            icon = RARITY_ICON.get(entry.card.rarity.upper(), "⚪")
            count_str = f"×{entry.count}" if entry.count > 1 else "  "
            lines.append(
                f"`{count_str}` {icon} **{entry.card.name}** — {entry.card.cost} mana"
            )
            total_dust += _dust_cost(entry.card.rarity.upper(), entry.count)
        embed.add_field(
            name=f"— {label} ({len(entries)}) —",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text=f"Total cards: {deck.total_cards}  |  Crafting cost: {total_dust:,} dust")
    return embed


class DeckCommands(commands.Cog):
    """Commands for Hearthstone deck codes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.hs_client = HSJsonClient()
        self.decoder = DeckDecoder(self.hs_client)
        self.image_gen = ImageGenerator(self.hs_client)

    @app_commands.command(name="deck", description="Decode a Hearthstone deck code into a card list.")
    @app_commands.describe(code="The Hearthstone deck code to decode")
    async def deck(self, interaction: discord.Interaction, code: str) -> None:
        await interaction.response.defer()
        try:
            deck = await self.decoder.decode(code.strip())
            embed = build_deck_embed(deck)
            await interaction.followup.send(embed=embed)
        except ValueError as exc:
            await interaction.followup.send(f"❌ Invalid deck code: {exc}", ephemeral=True)
        except Exception:
            log.exception("Unexpected error in /deck")
            await interaction.followup.send("❌ Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="deckimage", description="Render a visual image of a Hearthstone deck.")
    @app_commands.describe(code="The Hearthstone deck code to render")
    async def deckimage(self, interaction: discord.Interaction, code: str) -> None:
        await interaction.response.defer()
        try:
            deck = await self.decoder.decode(code.strip())
            image_bytes = await self.image_gen.generate_deck_image(deck)
            file = discord.File(fp=image_bytes, filename="deck.png")
            await interaction.followup.send(
                content=f"**{deck.hero_class}** — {deck.format_label}",
                file=file,
            )
        except ValueError as exc:
            await interaction.followup.send(f"❌ Invalid deck code: {exc}", ephemeral=True)
        except Exception:
            log.exception("Unexpected error in /deckimage")
            await interaction.followup.send("❌ Something went wrong. Please try again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DeckCommands(bot))
