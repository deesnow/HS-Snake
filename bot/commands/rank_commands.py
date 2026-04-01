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

from datetime import datetime, timezone

from bot.services import leaderboard_cache
from bot.services.db import get_db
from bot.services.leaderboard_client import LeaderboardEntry

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
        self._full_refresh_running = False
        self._quick_refresh_started = False
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
        while not getattr(self, "_quick_refresh_started", False):
            await asyncio.sleep(1)

    @tasks.loop(minutes=3)
    async def _full_refresh(self) -> None:
        """Fetch all pages every 3 minutes to keep the full leaderboard current."""
        self._full_refresh_running = True

        async def fetch_and_log(region, mode):
            try:
                count, season_id, _ = await leaderboard_cache.refresh_pages(region, mode)
                log.info("Full refresh %s/%s: %d rows (season %s)", region, mode, count, season_id)
            except Exception:
                log.exception("Full refresh failed for %s/%s", region, mode)

        await asyncio.gather(*(fetch_and_log(r, m) for r, m in _WARM_COMBOS))
        self._full_refresh_running = False
        if not getattr(self, "_quick_refresh_started", False):
            self._quick_refresh.start()
            self._quick_refresh_started = True

    # ── Slash commands ────────────────────────────────────────────────────────

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
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO user_battletags (discord_id, region, battletag)
                VALUES ($1, $2, $3)
                ON CONFLICT (discord_id, region) DO UPDATE SET battletag = EXCLUDED.battletag
                """,
                str(interaction.user.id), region.value, bt,
            )
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
        async with get_db() as conn:
            result = await conn.execute(
                "DELETE FROM user_battletags WHERE discord_id = $1 AND region = $2",
                str(interaction.user.id), region.value,
            )
        # asyncpg returns a command tag like "DELETE 1" or "DELETE 0"
        deleted = int(result.split()[-1])
        if deleted:
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
        await interaction.response.send_message("⏳ Looking up your rank...", ephemeral=False)
        async with get_db() as conn:
            if region:
                rows = await conn.fetch(
                    "SELECT region, battletag FROM user_battletags "
                    "WHERE discord_id = $1 AND region = $2 ORDER BY region",
                    str(interaction.user.id), region.value,
                )
                log.debug("/rank: user=%s region=%s rows=%r", interaction.user.id, region.value, rows)
            else:
                rows = await conn.fetch(
                    "SELECT region, battletag FROM user_battletags "
                    "WHERE discord_id = $1 ORDER BY region",
                    str(interaction.user.id),
                )
                log.debug("/rank: user=%s all regions rows=%r", interaction.user.id, rows)
        if not rows:
            tip = f"**{region.value}**" if region else "any region"
            await interaction.edit_original_response(
                content=f"❌ No BattleTag registered for {tip}. Use `/rankset` first."
            )
            return
        try:
            discord_id = str(interaction.user.id)
            sections = await asyncio.gather(*[
                self._build_section(row["battletag"], row["region"], mode, discord_id)
                for row in rows
            ])
            log.debug("/rank: user=%s sections=%r", interaction.user.id, sections)
            await interaction.edit_original_response(content="\n\n".join(sections))
        except RuntimeError as exc:
            log.exception("/rank: user=%s RuntimeError: %s", interaction.user.id, exc)
            await interaction.edit_original_response(content=f"⏳ {exc}")
        except Exception:
            log.exception("/rank lookup failed for user=%s", interaction.user)
            await interaction.edit_original_response(
                content="❌ Something went wrong fetching the leaderboard. Please try again."
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _build_section(self, battletag, region, mode, discord_id):
        if mode is not None:
            return await self._section_single(battletag, region, mode.value, mode.name, discord_id)
        return await self._section_default(battletag, region, discord_id)

    async def _section_single(self, battletag, region, mode, mode_label, discord_id):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry, season_id = await self._fetch_entry(battletag, region, mode, discord_id)
        async with get_db() as conn:
            season_row = await conn.fetchrow(
                """
                SELECT season_score, days_counted FROM player_season_score
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, mode, season_id,
            )
            best_rank = await conn.fetchval(
                """
                SELECT MIN(best_rank) FROM player_daily_best
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, mode, season_id,
            )
            today_dps = await conn.fetchval(
                """
                SELECT dps FROM player_daily_dps
                WHERE discord_id = $1 AND region = $2 AND mode = $3
                  AND season_id = $4 AND date_utc = $5
                """,
                discord_id, region, mode, season_id, today,
            )

        season_score = f"{season_row['season_score']:.2f}" if season_row else "-"
        days_counted = f"{season_row['days_counted']}" if season_row else "-"
        best_rank_str = f"{best_rank}" if best_rank is not None else "-"
        today_dps_str = f"{today_dps:.2f}" if today_dps is not None else "-"

        header = f"🏆 **{battletag}** — {region}  ·  Season {season_id}"
        if entry is None:
            rank_str = "_Not found in top ranks this season._"
        else:
            rank_str = f"**#{entry.rank}**"
            if entry.rating:
                rank_str += f"  (MMR {entry.rating})"
        return (
            f"{header}\n"
            f"{mode_label}  ->  {rank_str}\n"
            f"Season Score: {season_score}   Days Counted: {days_counted}   Best Rank: {best_rank_str}   Today's Score: {today_dps_str}"
        )

    async def _section_default(self, battletag, region, discord_id):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (std_entry, std_season), (wild_entry, wild_season) = await asyncio.gather(
            self._fetch_entry(battletag, region, "standard", discord_id),
            self._fetch_entry(battletag, region, "wild", discord_id),
        )
        async with get_db() as conn:
            std_season_row = await conn.fetchrow(
                """
                SELECT season_score, days_counted FROM player_season_score
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, "standard", std_season,
            )
            std_best = await conn.fetchval(
                """
                SELECT MIN(best_rank) FROM player_daily_best
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, "standard", std_season,
            )
            std_today_dps = await conn.fetchval(
                """
                SELECT dps FROM player_daily_dps
                WHERE discord_id = $1 AND region = $2 AND mode = $3
                  AND season_id = $4 AND date_utc = $5
                """,
                discord_id, region, "standard", std_season, today,
            )
            wild_season_row = await conn.fetchrow(
                """
                SELECT season_score, days_counted FROM player_season_score
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, "wild", wild_season,
            )
            wild_best = await conn.fetchval(
                """
                SELECT MIN(best_rank) FROM player_daily_best
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, "wild", wild_season,
            )
            wild_today_dps = await conn.fetchval(
                """
                SELECT dps FROM player_daily_dps
                WHERE discord_id = $1 AND region = $2 AND mode = $3
                  AND season_id = $4 AND date_utc = $5
                """,
                discord_id, region, "wild", wild_season, today,
            )

        def _current(e):
            return f"#{e.rank}" if e else "-"

        def _best(v):
            return f"#{v}" if v is not None else "-"

        def _score(r):
            return f"{r['season_score']:.2f}" if r else "-"

        def _dps(v):
            return f"{v:.2f}" if v is not None else "-"

        std_current   = _current(std_entry)
        wild_current  = _current(wild_entry)
        std_best_str  = _best(std_best)
        wild_best_str = _best(wild_best)
        std_score     = _score(std_season_row)
        wild_score    = _score(wild_season_row)
        std_dps       = _dps(std_today_dps)
        wild_dps      = _dps(wild_today_dps)

        label_dps = "Today's Score"
        w = max(len(label_dps), len(std_current), len(wild_current),
                len(std_best_str), len(wild_best_str), len(std_score), len(wild_score),
                len(std_dps), len(wild_dps), 8)
        return "\n".join([
            f"🏆 **{battletag}** — {region}  ·  Season {std_season}",
            "```",
            f"  {'':<{w}}   Standard   Wild",
            f"  {'-'*w}   {'-'*8}   {'-'*8}",
            f"  {'Current Rank':<{w}}   {std_current:<8}   {wild_current:<8}",
            f"  {'Best Rank':<{w}}   {std_best_str:<8}   {wild_best_str:<8}",
            f"  {'Season Score':<{w}}   {std_score:<8}   {wild_score:<8}",
            f"  {label_dps:<{w}}   {std_dps:<8}   {wild_dps:<8}",
            "```",
        ])

    @staticmethod
    async def _fetch_entry(battletag, region, mode, discord_id):
        needle = battletag.lower().split("#")[0]
        async with get_db() as conn:
            # Current season is always derived from the live leaderboard, never from player data.
            season_id = await conn.fetchval(
                "SELECT MAX(season_id) FROM ldb_current_entries WHERE region = $1 AND mode = $2",
                region.upper(), mode.lower(),
            )
            if season_id is None:
                raise RuntimeError(
                    f"Leaderboard data for {region}/{mode} is not available yet. "
                    "The bot is still loading data in the background — please try again in a few minutes."
                )

            # Primary: most recent tracked observation for this registered player.
            row = await conn.fetchrow(
                """
                SELECT rank, rating
                FROM player_rank_log
                WHERE discord_id = $1 AND region = $2 AND mode = $3
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                discord_id, region.upper(), mode.lower(),
            )
            if row:
                return LeaderboardEntry(
                    rank=row["rank"],
                    battletag=needle,
                    battletag_orig=battletag,
                    rating=row["rating"],
                ), season_id

            # Fallback: direct index lookup in ldb_current_entries.
            ldb_row = await conn.fetchrow(
                """
                SELECT rank, battletag, battletag_orig, rating
                FROM ldb_current_entries
                WHERE region = $1 AND mode = $2 AND battletag = $3
                LIMIT 1
                """,
                region.upper(), mode.lower(), needle,
            )
            if ldb_row:
                return LeaderboardEntry(
                    rank=ldb_row["rank"],
                    battletag=ldb_row["battletag"],
                    battletag_orig=ldb_row["battletag_orig"],
                    rating=ldb_row["rating"],
                ), season_id

            return None, season_id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankCommands(bot))
