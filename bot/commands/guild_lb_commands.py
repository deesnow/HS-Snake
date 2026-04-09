"""
Guild leaderboard slash command: /glb

Shows a ranked list of season scores filtered to members of the calling Discord server.
"""
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.db import get_db

log = logging.getLogger(__name__)

_REGION_CHOICES = [
    app_commands.Choice(name="EU", value="EU"),
    app_commands.Choice(name="US", value="US"),
    app_commands.Choice(name="AP", value="AP"),
]

_MODE_CHOICES = [
    app_commands.Choice(name="Standard", value="standard"),
    app_commands.Choice(name="Wild",     value="wild"),
]

_TOP_CHOICES = [
    app_commands.Choice(name="Top 10",  value=10),
    app_commands.Choice(name="Top 20",  value=20),
    app_commands.Choice(name="All",     value=0),
]

_SEASON_CHOICES = [
    app_commands.Choice(name="Current",  value="current"),
    app_commands.Choice(name="Previous", value="previous"),
]


class GuildLbCommands(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /glb ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="glb",
        description="Show the season score leaderboard for members of this server.",
    )
    @app_commands.describe(
        region="Leaderboard region",
        mode="Game mode: Standard or Wild",
        top="How many entries to show",
        season="Current or previous season (default: Current)",
    )
    @app_commands.choices(
        region=_REGION_CHOICES,
        mode=_MODE_CHOICES,
        top=_TOP_CHOICES,
        season=_SEASON_CHOICES,
    )
    async def glb(
        self,
        interaction: discord.Interaction,
        region: app_commands.Choice[str],
        mode: app_commands.Choice[str],
        top: app_commands.Choice[int],
        season: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer()

        season_value = season.value if season is not None else "current"

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "❌ This command can only be used inside a server.", ephemeral=True
            )
            return

        async with get_db() as conn:
            # ── Resolve current season_id ────────────────────────────────────
            current_season_id = await conn.fetchval(
                "SELECT MAX(season_id) FROM ldb_current_entries WHERE region=$1 AND mode=$2",
                region.value, mode.value,
            )
            if current_season_id is None:
                await interaction.followup.send(
                    f"❌ No leaderboard data found for **{region.value} · {mode.name}**."
                )
                return

            if season_value == "previous":
                season_id = await conn.fetchval(
                    """
                    SELECT MAX(season_id)
                    FROM player_season_score
                    WHERE region = $1 AND mode = $2 AND season_id < $3
                    """,
                    region.value, mode.value, current_season_id,
                )
                if season_id is None:
                    await interaction.followup.send(
                        f"❌ No previous season data found for **{region.value} · {mode.name}**."
                    )
                    return
            else:
                season_id = current_season_id

            # ── Fetch scores ─────────────────────────────────────────────────
            rows = await conn.fetch(
                """
                SELECT pss.discord_id, ub.battletag, pss.season_score, pss.days_counted
                FROM player_season_score pss
                JOIN user_battletags ub
                  ON ub.discord_id = pss.discord_id AND ub.region = pss.region
                WHERE pss.region = $1 AND pss.mode = $2 AND pss.season_id = $3
                ORDER BY pss.season_score DESC
                """,
                region.value, mode.value, season_id,
            )

        # ── Filter to guild members ───────────────────────────────────────────
        guild_rows = [
            row for row in rows
            if guild.get_member(int(row["discord_id"])) is not None
        ]

        if not guild_rows:
            await interaction.followup.send(
                f"No members of this server have season score data for "
                f"**{region.value} · {mode.name} · Season {season_id}**."
            )
            return

        # ── Apply limit ───────────────────────────────────────────────────────
        if top.value != 0:
            guild_rows = guild_rows[: top.value]

        # ── Format output ─────────────────────────────────────────────────────
        season_label = "Current" if season_value == "current" else "Previous"
        header = (
            f"🏆 Guild Leaderboard — {region.value} · {mode.name} · "
            f"Season {season_id} ({season_label})\n"
        )

        col_bt    = max(len(r["battletag"]) for r in guild_rows)
        col_bt    = max(col_bt, len("BattleTag"))
        col_score = max(len(f"{r['season_score']:.2f}") for r in guild_rows)
        col_score = max(col_score, len("Score"))
        col_days  = max(len(str(r["days_counted"])) for r in guild_rows)
        col_days  = max(col_days, len("Days"))

        sep   = f"{'---':<4}  {'-' * col_bt}  {'-' * col_score}  {'-' * col_days}"
        hdr   = f"{'#':<4}  {'BattleTag':<{col_bt}}  {'Score':>{col_score}}  {'Days':>{col_days}}"

        lines = [hdr, sep]
        for i, row in enumerate(guild_rows, start=1):
            score = f"{row['season_score']:.2f}"
            lines.append(
                f"{f'{i}.':<4}  {row['battletag']:<{col_bt}}  {score:>{col_score}}  {row['days_counted']:>{col_days}}"
            )

        table = "```\n" + "\n".join(lines) + "\n```"
        await interaction.followup.send(header + table)

        log.info(
            "/glb guild=%s region=%s mode=%s season=%s top=%s -> %d rows",
            guild.id, region.value, mode.value, season_id, top.value, len(guild_rows),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildLbCommands(bot))
