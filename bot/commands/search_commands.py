"""
Card search slash command: /cardsearch

Presents three Select dropdowns (Mana Cost, Class, Card Type) plus an optional
name text field. Clicking 🔍 Search runs the query; results are paginated with
◀ Prev / ▶ Next buttons. The entire UI is ephemeral — only the invoking user
sees it.
"""
import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.hs_json_client import HSJsonClient
from bot.services.models import CardInfo

log = logging.getLogger(__name__)

PAGE_SIZE = 10
MAX_RESULTS = 100

RARITY_ICON = {
    "FREE":      "⚪",
    "COMMON":    "⚪",
    "RARE":      "🔵",
    "EPIC":      "🟣",
    "LEGENDARY": "🟡",
}

# ── Filter option definitions ──────────────────────────────────────────────────

_COST_OPTIONS = [
    discord.SelectOption(label="Any", value="any"),
    discord.SelectOption(label="0", value="0"),
    discord.SelectOption(label="1", value="1"),
    discord.SelectOption(label="2", value="2"),
    discord.SelectOption(label="3", value="3"),
    discord.SelectOption(label="4", value="4"),
    discord.SelectOption(label="5", value="5"),
    discord.SelectOption(label="6", value="6"),
    discord.SelectOption(label="7", value="7"),
    discord.SelectOption(label="8", value="8"),
    discord.SelectOption(label="9", value="9"),
    discord.SelectOption(label="10+", value="10plus"),
]

_CLASS_OPTIONS = [
    discord.SelectOption(label="Any",          value="any"),
    discord.SelectOption(label="Druid",        value="DRUID"),
    discord.SelectOption(label="Hunter",       value="HUNTER"),
    discord.SelectOption(label="Mage",         value="MAGE"),
    discord.SelectOption(label="Paladin",      value="PALADIN"),
    discord.SelectOption(label="Priest",       value="PRIEST"),
    discord.SelectOption(label="Rogue",        value="ROGUE"),
    discord.SelectOption(label="Shaman",       value="SHAMAN"),
    discord.SelectOption(label="Warlock",      value="WARLOCK"),
    discord.SelectOption(label="Warrior",      value="WARRIOR"),
    discord.SelectOption(label="Demon Hunter", value="DEMONHUNTER"),
    discord.SelectOption(label="Death Knight", value="DEATHKNIGHT"),
    discord.SelectOption(label="Neutral",      value="NEUTRAL"),
]

_TYPE_OPTIONS = [
    discord.SelectOption(label="Any",      value="any"),
    discord.SelectOption(label="Minion",   value="MINION"),
    discord.SelectOption(label="Spell",    value="SPELL"),
    discord.SelectOption(label="Weapon",   value="WEAPON"),
    discord.SelectOption(label="Hero",     value="HERO"),
    discord.SelectOption(label="Location", value="LOCATION"),
]


# ── Helper ─────────────────────────────────────────────────────────────────────

def _card_subtype(card: CardInfo) -> str:
    """Return spell school or minion race label, or empty string."""
    t = card.card_type.upper()
    if t == "SPELL" and card.spell_school:
        return card.spell_school.capitalize()
    if t == "MINION" and card.race and card.race.upper() not in ("", "INVALID"):
        return card.race.capitalize()
    return ""


def _card_line(card: CardInfo, show_cost: bool, show_class: bool, show_type: bool) -> str:
    icon = RARITY_ICON.get(card.rarity.upper(), "⚪")
    parts = []
    if show_cost:
        parts.append(f"{card.cost} mana")
    if show_class:
        parts.append(card.card_class.capitalize())
    if show_type:
        parts.append(card.card_type.capitalize())
    subtype = _card_subtype(card)
    if subtype:
        parts.append(f"({subtype})")
    suffix = f" — {' '.join(parts)}" if parts else f" — {subtype}" if subtype else ""
    return f"{icon} **{card.name}**{suffix}"


def _build_results_embed(
    cards: list[CardInfo],
    page: int,
    cost_filter: str,
    class_filter: str,
    type_filter: str,
) -> discord.Embed:
    total = len(cards)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start = page * PAGE_SIZE
    slice_ = cards[start: start + PAGE_SIZE]

    show_cost  = cost_filter  == "any"
    show_class = class_filter == "any"
    show_type  = type_filter  == "any"

    embed = discord.Embed(
        title=f"🔎 Card Search Results  ({total} found)",
        colour=0x1A1A2E,
    )
    embed.description = "\n".join(
        _card_line(c, show_cost, show_class, show_type) for c in slice_
    ) or "_No cards found._"
    embed.set_footer(text=(
        f"Page {page + 1}/{total_pages}  ·  "
        f"Cost: {cost_filter}  ·  Class: {class_filter}  ·  Type: {type_filter}"
    ))
    return embed


# ── Results view (shown after searching) ─────────────────────────────────────

class CardResultsView(discord.ui.View):
    """Paginated results with a card-image picker dropdown."""

    def __init__(
        self,
        hs_client: HSJsonClient,
        results: list[CardInfo],
        page: int,
        cost: str,
        card_class: str,
        card_type: str,
        name_query: str,
    ) -> None:
        super().__init__(timeout=180)
        self.hs_client = hs_client
        self._results = results
        self._page = page
        self._cost = cost
        self._class = card_class
        self._type = card_type
        self.name_query = name_query

        # Row 0: card image picker for the current page
        start = page * PAGE_SIZE
        page_cards = results[start: start + PAGE_SIZE]
        options = [
            discord.SelectOption(label=c.name[:100], value=str(i))
            for i, c in enumerate(page_cards)
        ] or [discord.SelectOption(label="(no results)", value="__none__")]

        self.card_select = discord.ui.Select(
            placeholder="🖼 View card image…",
            options=options,
            disabled=not page_cards,
            row=0,
        )
        self.card_select.callback = self._on_card_pick
        self.add_item(self.card_select)

        self._update_nav_state()

    # ── Card image picker ──────────────────────────────────────────────

    async def _on_card_pick(self, interaction: discord.Interaction) -> None:
        value = self.card_select.values[0]
        if value == "__none__":
            await interaction.response.defer()
            return
        start = self._page * PAGE_SIZE
        card = self._results[start + int(value)]
        await interaction.response.send_message(f"⏳ Loading **{card.name}**…", ephemeral=True)
        try:
            image_bytes = await self.hs_client.get_card_image_bytes(card.card_id, card.dbf_id)
            file = discord.File(fp=io.BytesIO(image_bytes), filename=f"{card.card_id}.png")
            await interaction.delete_original_response()
            await interaction.followup.send(file=file, ephemeral=True)
        except Exception:
            log.exception("Error fetching card image in /cardsearch")
            await interaction.edit_original_response(content="❌ Something went wrong.")

    # ── Navigation buttons ─────────────────────────────────────────────

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        new_view = CardResultsView(
            self.hs_client, self._results, self._page - 1,
            self._cost, self._class, self._type, self.name_query,
        )
        embed = _build_results_embed(self._results, self._page - 1, self._cost, self._class, self._type)
        await interaction.edit_original_response(embed=embed, view=new_view)

    @discord.ui.button(label="▶ Next", style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        new_view = CardResultsView(
            self.hs_client, self._results, self._page + 1,
            self._cost, self._class, self._type, self.name_query,
        )
        embed = _build_results_embed(self._results, self._page + 1, self._cost, self._class, self._type)
        await interaction.edit_original_response(embed=embed, view=new_view)

    @discord.ui.button(label="🔙 New Search", style=discord.ButtonStyle.primary, row=1)
    async def new_search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = CardSearchView(self.hs_client, name_query=self.name_query)
        embed = discord.Embed(
            title="🔎 Card Search",
            description=(
                "Set your filters using the dropdowns below, then click **🔍 Search**."
                + (f"\n\n**Name filter:** `{self.name_query}`" if self.name_query else "")
            ),
            colour=0x1A1A2E,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.danger, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="_Search cancelled._", embed=None, view=None)

    def _update_nav_state(self) -> None:
        total_pages = max(1, (len(self._results) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.prev_button.disabled = self._page <= 0
        self.next_button.disabled = self._page >= total_pages - 1

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]


# ── Filter view (shown initially) ─────────────────────────────────────────────

class CardSearchView(discord.ui.View):
    """Interactive filter UI for /cardsearch."""

    def __init__(self, hs_client: HSJsonClient, name_query: str) -> None:
        super().__init__(timeout=180)
        self.hs_client = hs_client
        self.name_query = name_query

        # Filter state
        self._cost: str = "any"
        self._class: str = "any"
        self._type: str = "any"

        self.cost_select = discord.ui.Select(
            placeholder="Mana Cost: Any",
            options=_COST_OPTIONS,
            row=0,
        )
        self.cost_select.callback = self._on_cost
        self.add_item(self.cost_select)

        self.class_select = discord.ui.Select(
            placeholder="Class: Any",
            options=_CLASS_OPTIONS,
            row=1,
        )
        self.class_select.callback = self._on_class
        self.add_item(self.class_select)

        self.type_select = discord.ui.Select(
            placeholder="Type: Any",
            options=_TYPE_OPTIONS,
            row=2,
        )
        self.type_select.callback = self._on_type
        self.add_item(self.type_select)

    # ── Select callbacks ───────────────────────────────────────────────

    async def _on_cost(self, interaction: discord.Interaction) -> None:
        self._cost = self.cost_select.values[0]
        await interaction.response.defer()

    async def _on_class(self, interaction: discord.Interaction) -> None:
        self._class = self.class_select.values[0]
        await interaction.response.defer()

    async def _on_type(self, interaction: discord.Interaction) -> None:
        self._type = self.type_select.values[0]
        await interaction.response.defer()

    # ── Search / Cancel buttons ────────────────────────────────────────

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary, row=3)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="⏳ Searching cards…", embed=None, view=None)

        cost_arg: Optional[int] = None
        if self._cost != "any":
            cost_arg = -1 if self._cost == "10plus" else int(self._cost)

        class_arg: Optional[str] = None if self._class == "any" else self._class
        type_arg:  Optional[str] = None if self._type  == "any" else self._type

        log.info(
            "/cardsearch user=%s cost=%s class=%s type=%s name=%r",
            interaction.user, self._cost, self._class, self._type, self.name_query,
        )

        results = await self.hs_client.search_cards(
            cost=cost_arg,
            card_class=class_arg,
            card_type=type_arg,
            name_query=self.name_query,
            limit=MAX_RESULTS,
        )

        results_view = CardResultsView(
            self.hs_client, results, 0,
            self._cost, self._class, self._type, self.name_query,
        )
        embed = _build_results_embed(results, 0, self._cost, self._class, self._type)
        await interaction.edit_original_response(content=None, embed=embed, view=results_view)

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.danger, row=3)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="_Search cancelled._", embed=None, view=None)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]


# ── Cog ────────────────────────────────────────────────────────────────────────

class SearchCommands(commands.Cog):
    """Interactive card search command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.hs_client = HSJsonClient()

    @app_commands.command(name="cardsearch", description="Search Hearthstone cards with interactive filters.")
    @app_commands.describe(name="Optional: filter by card name (partial match)")
    async def cardsearch(self, interaction: discord.Interaction, name: str = "") -> None:
        log.info("/cardsearch invoked user=%s guild=%s channel=%s name=%r",
                 interaction.user, interaction.guild_id, interaction.channel_id, name)
        await interaction.response.send_message("⏳ Loading card search…", ephemeral=True)

        view = CardSearchView(self.hs_client, name_query=name.strip())

        embed = discord.Embed(
            title="🔎 Card Search",
            description=(
                "Set your filters using the dropdowns below, then click **🔍 Search**.\n"
                + (f"\n**Name filter:** `{name.strip()}`" if name.strip() else "")
            ),
            colour=0x1A1A2E,
        )
        await interaction.edit_original_response(content=None, embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchCommands(bot))
