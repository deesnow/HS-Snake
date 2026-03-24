"""
Legend rank slash commands: /rank, /rankset, /rankremove.

Multi-region support: one BattleTag per (discord_id, region).
/rankset    -- register/update a BattleTag for a specific region
/rankremove -- remove registration for a region
/rank       -- show ranks across all registered regions (or filter by region/mode)
"""
import asyncio
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.services import leaderboard_cache
from bot.services.db import get_db

log = logging.getLogger(__name__)

_BATTLETAG_RE = re.compile(r"^\w+#\d+$")

# Pages considered "top" — refreshed every 5 min for near-realtime top-rank data.
# 20 pages × 25 entries = top ~500 players per combo.
_TOP_PAGES = 20

# All combos refreshed on both schedules.
_WARM_COMBOS = [
    ("EU", "standard"), ("EU", "wild"),
    ("US", "standard"), ("US", "wild"),
    ("AP", "standard"), ("AP", "wild"),
]

_MODE_CHOICES = [
    app_commands.Choice(name="Standard",          value="standard"),
    app_commands.Choice(name="Wild",              value="wild"),
    app_commands.Choice(name="Classic",           value="classic"),
    app_commands.Choice(name="Battlegrounds",     value="battlegrounds"),
    app_commands.Choice(name="Battlegrounds Duo", value="battlegroundsduo"),
    app_commands.Choice(name="Arena",             value="arena"),
    app_commands.Choice(name="Twist",             value="twist"),
]

_REGION_CHOICES = [
    app_commands.Choice(name="EU", value="EU"),
    app_commands.Choice(name="US", value="US"),
    app_commands.Choice(name="AP", value="AP"),
]


class RankCommands(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self._quick_refresh.start()
        self._full_refresh.start()

    async def cog_unload(self) -> None:
        self._quick_refresh.cancel()
        self._full_refresh.cancel()

    # ── Background refresh tasks ──────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def _quick_refresh(self) -> None:
        """Refresh top pages (~500 players) every 5 minutes."""
        for region, mode in _WARM_COMBOS:
            try:
                count, season_id, _ = await leaderboard_cache.refresh_pages(
                    region, mode, max_page=_TOP_PAGES
                )
                log.debug("Quick refresh %s/%s: %d rows (season %s)", region, mode, count, season_id)
            except Exception:
                log.exception("Quick refresh failed for %s/%s", region, mode)

    @_quick_refresh.before_loop
    async def _before_quick_refresh(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def _full_refresh(self) -> None:
        """Fetch all pages every 30 minutes to keep the full leaderboard current."""
        for region, mode in _WARM_COMBOS:
            try:
                count, season_id, _ = await leaderboard_cache.refresh_pages(region, mode)
                log.info("Full refresh %s/%s: %d rows (season %s)", region, mode, count, season_id)
            except Exception:
                log.exception("Full refresh failed for %s/%s", region, mode)

    @_full_refresh.before_loop
    async def _before_full_refresh(self) -> None:
        await self.bot.wait_until_ready()
        # Offset so the full refresh doesn't compete with the first quick refresh.
        await asyncio.sleep(60)

    @app_commands.command(
        name="rankset",
        description="Register your BattleTag for a region. Run once per region you play in.",
    )
    @app_commands.describe(
        battletag="Your BattleTag for this region, e.g. Player#1234",
        region="Region: EU, US or AP",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    async def rankset(
        self,
        interaction: discord.Interaction,
        battletag: str,
        region: app_commands.Choice[str],
    ) -> None:
        bt = battletag.strip()
        if not _BATTLETAG_RE.match(bt):
            await interaction.response.send_message(
                "❌ Invalid BattleTag format. Use `Name#1234`.",
                ephemeral=True,
            )
            return
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO user_battletags (discord_id, region, battletag)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_id, region) DO UPDATE SET battletag=excluded.battletag
                """,
                (str(interaction.user.id), region.value, bt),
            )
            await db.commit()
        log.info("/rankset user=%s battletag=%r region=%s", interaction.user, bt, region.value)
        await interaction.response.send_message(
            f"✅ Registered **{bt}** for **{region.value}**. "
            "Use `/rank` to check your rank, or `/rankset` again for another region.",
            ephemeral=True,
        )

    @app_commands.command(
        name="rankremove",
        description="Remove your registered BattleTag for a specific region.",
    )
    @app_commands.describe(region="Region to remove")
    @app_commands.choices(region=_REGION_CHOICES)
    async def rankremove(
        self,
        interaction: discord.Interaction,
        region: app_commands.Choice[str],
    ) -> None:
        async with get_db() as db:
            cursor = await db.execute(
                "DELETE FROM user_battletags WHERE discord_id = ? AND region = ?",
                (str(interaction.user.id), region.value),
            )
            await db.commit()
        if cursor.rowcount:
            await interaction.response.send_message(
                f"✅ Removed your **{region.value}** registration.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ No **{region.value}** registration found.", ephemeral=True
            )

    @app_commands.command(
        name="rank",
        description="Show your Hearthstone legend rank across all registered regions.",
    )
    @app_commands.describe(
        mode="Game mode to check (default: Standard + Wild)",
        region="Limit to one region (default: all registered regions)",
    )
    @app_commands.choices(mode=_MODE_CHOICES, region=_REGION_CHOICES)
    async def rank(
        self,
        interaction: discord.Interaction,
        mode: Optional[app_commands.Choice[str]] = None,
        region: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.send_message("⏳ Looking up your rank...", ephemeral=True)
        async with get_db() as db:
            if region:
                cursor = await db.execute(
                    "SELECT region, battletag FROM user_battletags "
                    "WHERE discord_id = ? AND region = ? ORDER BY region",
                    (str(interaction.user.id), region.value),
                )
            else:
                cursor = await db.execute(
                    "SELECT region, battletag FROM user_battletags "
                    "WHERE discord_id = ? ORDER BY region",
                    (str(interaction.user.id),),
                )
            rows = await cursor.fetchall()
        if not rows:
            tip = f"**{region.value}**" if region else "any region"
            await interaction.edit_original_response(
                content=f"❌ No BattleTag registered for {tip}. Use `/rankset` first."
            )
            return
        try:
            tasks = [
                self._build_section(row["battletag"], row["region"], mode)
                for row in rows
            ]
            sections = await asyncio.gather(*tasks)
            await interaction.edit_original_response(content="\n\n".join(sections))
        except RuntimeError as exc:
            await interaction.edit_original_response(content=f"⏳ {exc}")
        except Exception:
            log.exception("/rank lookup failed for user=%s", interaction.user)
            await interaction.edit_original_response(
                content="❌ Something went wrong fetching the leaderboard. Please try again."
            )

    async def _build_section(self, battletag, region, mode):
        if mode is not None:
            return await self._section_single(battletag, region, mode.value, mode.name)
        return await self._section_default(battletag, region)

    async def _section_single(self, battletag, region, mode, mode_label):
        entry, season_id = await self._fetch_entry(battletag, region, mode)
        header = f"🏆 **{battletag}** — {region}  ·  Season {season_id}"
        if entry is None:
            rank_str = "_Not found in top ranks this season._"
        else:
            rank_str = f"**#{entry.rank}**"
            if entry.rating:
                rank_str += f"  (MMR {entry.rating})"
        return f"{header}\n{mode_label}  ->  {rank_str}"

    async def _section_default(self, battletag, region):
        (std_entry, std_season), (wild_entry, _) = await asyncio.gather(
            self._fetch_entry(battletag, region, "standard"),
            self._fetch_entry(battletag, region, "wild"),
        )

        def _r(e):
            return f"#{e.rank}" if e else "-"

        std_str  = _r(std_entry)
        wild_str = _r(wild_entry)
        w = max(len(std_str), len(wild_str), 8)
        return "\n".join([
            f"🏆 **{battletag}** — {region}  ·  Season {std_season}",
            "```",
            f"  {'Standard':<{w}}   Wild",
            "  " + "-" * (w + 8),
            f"  {std_str:<{w}}   {wild_str}",
            "```",
        ])

    @staticmethod
    async def _fetch_entry(battletag, region, mode):
        entries, season_id, _ = await leaderboard_cache.get_snapshot(region, mode)
        if not entries:
            # DB not yet populated — background fetch still in progress
            raise RuntimeError(
                f"Leaderboard data for {region}/{mode} is not available yet. "
                "The bot is still loading data in the background — please try again in a few minutes."
            )
        # Blizzard API returns names without the #NNNN discriminator
        needle = battletag.lower().split("#")[0]
        entry = next((e for e in entries if e.battletag == needle), None)
        return entry, season_id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankCommands(bot))
