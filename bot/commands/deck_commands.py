"""
Deck-related slash commands: /deck, /analyzedeck, /deckimage.
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

RARITY_ICON = {
    "FREE":      "⚪",
    "COMMON":    "⚪",
    "RARE":      "🔵",
    "EPIC":      "🟣",
    "LEGENDARY": "🟡",
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
    if rarity == "LEGENDARY":
        return per_copy
    return per_copy * count


def _total_dust(deck) -> int:
    return sum(_dust_cost(e.card.rarity.upper(), e.count) for e in deck.cards)


def build_simple_deck_text(deck, code: str) -> str:
    """Return the simplified plain-text deck list matching the HS export style."""
    sorted_entries = sorted(deck.cards, key=lambda e: (e.card.cost, e.card.name))

    # Longest card line for separator length (icon + count + cost + name)
    longest = max(
        (len(f"{entry.count}x ({entry.card.cost}) {entry.card.name}") for entry in sorted_entries),
        default=20,
    )
    separator = "─" * (longest + 4)  # +4 for the icon and spacing

    lines = [
        f"# **{deck.hero_class}**",
        f"**Cost:** {_total_dust(deck):,} 💠",
        f"**Format:** {deck.format_label}",
        separator,
    ]
    for entry in sorted_entries:
        icon = RARITY_ICON.get(entry.card.rarity.upper(), "⚪")
        lines.append(f"{icon} {entry.count}x ({entry.card.cost}) {entry.card.name}")

    if deck.etc_sideboard_cards:
        lines.append(separator)
        lines.append("**E.T.C. Band Manager:**")
        for entry in sorted(deck.etc_sideboard_cards, key=lambda e: e.card.name):
            icon = RARITY_ICON.get(entry.card.rarity.upper(), "⚪")
            lines.append(f"{icon} ({entry.card.cost}) {entry.card.name}")

    lines.append(f"\n**Deck Code:**\n{code}")
    return "\n".join(lines)


TYPE_LABELS = {
    "MINION":   "Minions",
    "SPELL":    "Spells",
    "WEAPON":   "Weapons",
    "HERO":     "Heroes",
    "LOCATION": "Locations",
    "OTHER":    "Other",
}


def _subtype(card) -> str:
    """Return the tribe (minion) or spell school, or '-' if none."""
    if card.race:
        return card.race.capitalize()
    if card.spell_school:
        return card.spell_school.capitalize()
    return "-"


def _table_block(entries) -> str:
    """Render a section of cards as a monospace code-block table."""
    if not entries:
        return ""

    sorted_e = sorted(entries, key=lambda e: (e.card.cost, e.card.name))

    # Column widths
    name_w    = max(len(e.card.name) for e in sorted_e)
    subtype_w = max(len(_subtype(e.card)) for e in sorted_e)
    name_w    = max(name_w, 4)
    subtype_w = max(subtype_w, 4)

    header = f"  {'Cost':<4} {'Cnt':<3} {'Name':<{name_w}}  {'Type':<{subtype_w}}"
    sep    = "─" * len(header)
    rows   = [header, sep]

    for e in sorted_e:
        rar  = RARITY_ICON.get(e.card.rarity.upper(), "⚪")
        cost = str(e.card.cost)
        cnt  = f"×{e.count}"
        name = e.card.name
        sub  = _subtype(e.card)
        rows.append(f"{rar} {cost:<4} {cnt:<3} {name:<{name_w}}  {sub:<{subtype_w}}")

    return "```\n" + "\n".join(rows) + "\n```"


def _mana_curve_block(deck) -> str:
    """Render a mana curve as a vertical bar chart in a code block."""
    curve: dict[str, int] = {}
    for entry in deck.cards:
        cost = entry.card.cost
        key = "7+" if cost >= 7 else str(cost)
        curve[key] = curve.get(key, 0) + entry.count

    labels = [str(i) for i in range(7)] + ["7+"]
    counts = [curve.get(lbl, 0) for lbl in labels]
    max_count = max(counts) or 1
    bar_height = 8

    rows = []
    for row in range(bar_height, 0, -1):
        line = ""
        for count in counts:
            filled = round(count / max_count * bar_height)
            line += " █ " if filled >= row else "   "
        rows.append(line)

    rows.append("─" * (len(labels) * 3))
    rows.append("".join(f"{lbl:^3}" for lbl in labels))
    rows.append("".join(f"{c:^3}" for c in counts))

    return "```\n" + "\n".join(rows) + "\n```"


def build_deck_embed(deck) -> discord.Embed:
    """Return the detailed grouped embed (used by /deckanalyze)."""
    total_dust = _total_dust(deck)
    title = f"{deck.hero_class} — {deck.format_label}"
    embed = discord.Embed(title=title, colour=0x1A1A2E)
    embed.description = f"**Cost:** {total_dust:,} 💠  |  **Cards:** {deck.total_cards}"

    type_order = ["MINION", "SPELL", "WEAPON", "LOCATION", "HERO", "OTHER"]
    grouped: dict[str, list] = {t: [] for t in type_order}

    for entry in deck.cards:
        ct = entry.card.card_type.upper()
        grouped.get(ct, grouped["OTHER"]).append(entry)

    for type_key in type_order:
        entries = grouped[type_key]
        if not entries:
            continue
        label = f"{TYPE_LABELS.get(type_key, type_key)} ({len(entries)})"
        block = _table_block(entries)
        # Discord embed field value limit is 1024 chars; truncate if needed
        if len(block) > 1024:
            block = block[:1020] + "\n```"
        embed.add_field(name=label, value=block, inline=False)

    if deck.etc_sideboard_cards:
        block = _table_block(deck.etc_sideboard_cards)
        if len(block) > 1024:
            block = block[:1020] + "\n```"
        embed.add_field(name="E.T.C. Band Manager sideboard (3)", value=block, inline=False)

    embed.add_field(name="Mana Curve", value=_mana_curve_block(deck), inline=False)

    return embed


class DeckCommands(commands.Cog):
    """Commands for Hearthstone deck codes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.hs_client = HSJsonClient()
        self.decoder = DeckDecoder(self.hs_client)
        self.image_gen = ImageGenerator(self.hs_client)

    @app_commands.command(name="deck", description="Show a simple card list for a Hearthstone deck code.")
    @app_commands.describe(code="The Hearthstone deck code to decode")
    async def deck(self, interaction: discord.Interaction, code: str) -> None:
        log.info("/deck user=%s guild=%s channel=%s code=%.40s", interaction.user, interaction.guild_id, interaction.channel_id, code.strip())
        await interaction.response.send_message("⏳ Decoding deck…")
        try:
            deck = await self.decoder.decode(code.strip())
            text = build_simple_deck_text(deck, code.strip())
            await interaction.edit_original_response(content=text)
        except ValueError as exc:
            await interaction.edit_original_response(content=f"❌ Invalid deck code: {exc}")
        except Exception:
            log.exception("Unexpected error in /deck")
            await interaction.edit_original_response(content="❌ Something went wrong. Please try again.")

    @app_commands.command(name="deckanalyze", description="Show a detailed grouped analysis of a Hearthstone deck.")
    @app_commands.describe(code="The Hearthstone deck code to analyze")
    async def analyzedeck(self, interaction: discord.Interaction, code: str) -> None:
        log.info("/deckanalyze user=%s guild=%s channel=%s code=%.40s", interaction.user, interaction.guild_id, interaction.channel_id, code.strip())
        await interaction.response.send_message("⏳ Analysing deck…")
        try:
            deck = await self.decoder.decode(code.strip())
            embed = build_deck_embed(deck)
            await interaction.edit_original_response(content=None, embed=embed)
        except ValueError as exc:
            await interaction.edit_original_response(content=f"❌ Invalid deck code: {exc}")
        except Exception:
            log.exception("Unexpected error in /deckanalyze")
            await interaction.edit_original_response(content="❌ Something went wrong. Please try again.")

    @app_commands.command(name="deckimage", description="Render a visual image of a Hearthstone deck.")
    @app_commands.describe(code="The Hearthstone deck code to render")
    async def deckimage(self, interaction: discord.Interaction, code: str) -> None:
        log.info("/deckimage user=%s guild=%s channel=%s code=%.40s", interaction.user, interaction.guild_id, interaction.channel_id, code.strip())
        await interaction.response.defer()
        try:
            deck = await self.decoder.decode(code.strip())
            image_bytes = await self.image_gen.generate_deck_image(deck)
            file = discord.File(fp=image_bytes, filename="deck.png")
            await interaction.followup.send(
                content=f"**{deck.hero_class}** — {deck.format_label}  ·  {deck.total_cards} cards",
                file=file,
            )
        except ValueError as exc:
            await interaction.followup.send(content=f"❌ Invalid deck code: {exc}", ephemeral=True)
        except Exception:
            log.exception("Unexpected error in /deckimage")
            await interaction.followup.send(content="❌ Something went wrong. Please try again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DeckCommands(bot))

