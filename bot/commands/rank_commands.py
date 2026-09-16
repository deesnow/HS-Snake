"""
Legend rank slash commands: /rank, /rankset, /rankremove.

Multi-region support: one BattleTag per (discord_id, region).
/rankset    -- register/update a BattleTag for all regions at once
/rankremove -- remove registration for a region
/rank       -- show ranks across all registered regions (or filter by region/mode)
"""
import asyncio
import logging
import re
from typing import Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from datetime import date, datetime, timedelta, timezone

from bot.services import leaderboard_cache, rank_chart, rank_tracker_data
from bot.services.db import get_db
from bot.services.leaderboard_client import LeaderboardEntry
from bot.services.rank_scale import AxisMapper
from bot.services.season_id import (
    parse_season_arg,
    resolve_current_season_id,
    resolve_season_id_by_arg,
    resolve_season_month_range,
)

log = logging.getLogger(__name__)


async def _fetch_tracker_points_safe(conn, battletag, region, mode, start, end):
    """
    rank_tracker_data.fetch_tracker_points, degrading to "no tracker data" (an
    empty list, so callers fall back to player_rank_log alone — the
    pre-Bronze-Diamond behavior) if rank_tracker_matches.region doesn't exist
    yet. Guards a deploy-ordering race: the bot and rank-api containers start
    in parallel (docker-compose.yml has no depends_on between them), so the
    bot's queries against the region column can briefly run before
    decktrackerAPI's own migration has created it.
    """
    try:
        return await rank_tracker_data.fetch_tracker_points(conn, battletag, region, mode, start, end)
    except asyncpg.exceptions.UndefinedColumnError:
        log.warning(
            "rank_tracker_matches.region not available yet (deploy-ordering race?) — "
            "falling back to leaderboard-only data for battletag=%s region=%s mode=%s",
            battletag, region, mode,
        )
        return []


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

# player_rank_log is only populated for these two modes (see _WARM_COMBOS).
_CHART_MODE_CHOICES = [
    app_commands.Choice(name="Standard", value="standard"),
    app_commands.Choice(name="Wild",     value="wild"),
]

_TIMEFRAME_CHOICES = [
    app_commands.Choice(name="Season", value="season"),
    app_commands.Choice(name="Today",  value="today"),
]

_RANK_TYPE_CHOICES = [
    app_commands.Choice(name="Last", value="last"),
    app_commands.Choice(name="Best", value="best"),
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
        description="Register your BattleTag for all regions (EU, US, AP) at once.",
    )
    @app_commands.describe(
        battletag="Your BattleTag, e.g. Player#1234",
    )
    async def rankset(
        self,
        interaction: discord.Interaction,
        battletag: str,
    ) -> None:
        bt = battletag.strip()
        if not _BATTLETAG_RE.match(bt):
            await interaction.response.send_message(
                "❌ Invalid BattleTag format. Use `Name#1234`.",
                ephemeral=True,
            )
            return
        async with get_db() as conn:
            for region in ("EU", "US", "AP"):
                await conn.execute(
                    """
                    INSERT INTO user_battletags (discord_id, region, battletag)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (discord_id, region) DO UPDATE SET battletag = EXCLUDED.battletag
                    """,
                    str(interaction.user.id), region, bt,
                )
        log.info("/rankset user=%s battletag=%r all regions", interaction.user, bt)
        await interaction.response.send_message(
            f"✅ Registered **{bt}** for **EU, US and AP**. "
            "Use `/rank` to check your rank.",
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
            sections = await asyncio.gather(*[
                self._build_section(row["battletag"], row["region"], mode)
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

    @app_commands.command(
        name="rankchart",
        description="Show a chart of your rank progress this season.",
    )
    @app_commands.describe(
        mode="Game mode",
        region="Region your BattleTag is registered under",
        season="Current (default), 'previous', or a specific season number (e.g. 150)",
        timeframe="Season (day-by-day) or Today (intraday)",
        rank_type="For Season: last or best rank observed each day (default: Last)",
    )
    @app_commands.choices(
        mode=_CHART_MODE_CHOICES,
        region=_REGION_CHOICES,
        timeframe=_TIMEFRAME_CHOICES,
        rank_type=_RANK_TYPE_CHOICES,
    )
    async def rankchart(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        region: app_commands.Choice[str],
        season: Optional[str] = None,
        timeframe: Optional[app_commands.Choice[str]] = None,
        rank_type: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        timeframe_value = timeframe.value if timeframe else "season"
        rank_type_value = rank_type.value if rank_type else "last"

        season_raw = parse_season_arg(season)
        if season_raw is None:
            await interaction.response.send_message(
                "❌ Invalid `season` value. Use `current`, `previous`, or a season number (e.g. `150`).",
                ephemeral=True,
            )
            return

        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT battletag FROM user_battletags WHERE discord_id = $1 AND region = $2",
                str(interaction.user.id), region.value,
            )
        if row is None:
            await interaction.response.send_message(
                f"❌ No BattleTag registered for **{region.value}**. Use `/rankset` first.",
                ephemeral=True,
            )
            return
        battletag = row["battletag"]

        await interaction.response.defer()
        try:
            async with get_db() as conn:
                season_id = await resolve_season_id_by_arg(conn, region.value, mode.value, season_raw)
                if season_id is None:
                    raise RuntimeError(
                        f"Leaderboard data for {region.value}/{mode.value} is not available yet. "
                        "The bot is still loading data in the background — please try again in a few minutes."
                    )

                bt = battletag.lower()
                if timeframe_value == "today":
                    now = datetime.now(timezone.utc)
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    today_end = today_start + timedelta(days=1)

                    raw_times, raw_ranks = await rank_chart.fetch_today_series(
                        conn, bt, region.value, mode.value, season_id
                    )
                    tracker_points = await _fetch_tracker_points_safe(
                        conn, bt, region.value, mode.value, today_start, today_end
                    )
                    merged = rank_tracker_data.merge_with_rank_log(
                        list(zip(raw_times, raw_ranks)), tracker_points
                    )
                    if not merged:
                        await interaction.followup.send(
                            "❌ No rank data recorded yet today. Try again later."
                        )
                        return

                    mapper = AxisMapper([point for _, point in merged])
                    times = [ts for ts, _ in merged]
                    positions = [mapper.position(point) for _, point in merged]

                    legend_count = await rank_chart.fetch_today_legend_count(
                        conn, bt, region.value, mode.value, season_id
                    )
                    buf = rank_chart.render_today_chart(
                        battletag, region.value, mode.value, season_id,
                        times, positions, mapper, legend_count,
                    )
                else:
                    month_start, next_month_start, days_in_month = await resolve_season_month_range(
                        conn, region.value, mode.value, season_id
                    )
                    rank_log_points = await rank_chart.fetch_rank_log_points(
                        conn, bt, region.value, mode.value, season_id
                    )
                    tracker_points = await _fetch_tracker_points_safe(
                        conn, bt, region.value, mode.value, month_start, next_month_start
                    )
                    merged = rank_tracker_data.merge_with_rank_log(rank_log_points, tracker_points)
                    if not merged:
                        await interaction.followup.send(
                            "❌ No rank data recorded yet this season. Try again later."
                        )
                        return

                    mapper = AxisMapper([point for _, point in merged])
                    daily = rank_tracker_data.aggregate_by_day(merged, mapper)

                    days = list(range(1, days_in_month + 1))
                    positions = []
                    for d in days:
                        ohlc = daily.get(date(month_start.year, month_start.month, d))
                        if ohlc is None:
                            positions.append(None)
                        else:
                            positions.append(ohlc.low if rank_type_value == "best" else ohlc.close)

                    _, legend_counts = await rank_chart.fetch_season_legend_counts(
                        conn, bt, region.value, mode.value, season_id, days_in_month
                    )
                    buf = rank_chart.render_season_chart(
                        battletag, region.value, mode.value, season_id, rank_type_value,
                        days, positions, mapper, legend_counts,
                    )

            file = discord.File(fp=buf, filename="rankchart.png")
            await interaction.followup.send(file=file)
        except RuntimeError as exc:
            log.exception("/rankchart: user=%s RuntimeError: %s", interaction.user.id, exc)
            await interaction.followup.send(content=f"⏳ {exc}")
        except Exception:
            log.exception("/rankchart failed for user=%s", interaction.user)
            await interaction.followup.send(
                content="❌ Something went wrong generating the chart. Please try again."
            )

    @app_commands.command(
        name="rcc",
        description="Show a daily rank candlestick chart (open/close/best/worst per day).",
    )
    @app_commands.describe(
        mode="Game mode",
        region="Region your BattleTag is registered under",
        season="Current (default), 'previous', or a specific season number (e.g. 150)",
    )
    @app_commands.choices(
        mode=_CHART_MODE_CHOICES,
        region=_REGION_CHOICES,
    )
    async def rcc(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        region: app_commands.Choice[str],
        season: Optional[str] = None,
    ) -> None:
        season_raw = parse_season_arg(season)
        if season_raw is None:
            await interaction.response.send_message(
                "❌ Invalid `season` value. Use `current`, `previous`, or a season number (e.g. `150`).",
                ephemeral=True,
            )
            return

        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT battletag FROM user_battletags WHERE discord_id = $1 AND region = $2",
                str(interaction.user.id), region.value,
            )
        if row is None:
            await interaction.response.send_message(
                f"❌ No BattleTag registered for **{region.value}**. Use `/rankset` first.",
                ephemeral=True,
            )
            return
        battletag = row["battletag"]

        await interaction.response.defer()
        try:
            async with get_db() as conn:
                season_id = await resolve_season_id_by_arg(conn, region.value, mode.value, season_raw)
                if season_id is None:
                    raise RuntimeError(
                        f"Leaderboard data for {region.value}/{mode.value} is not available yet. "
                        "The bot is still loading data in the background — please try again in a few minutes."
                    )

                bt = battletag.lower()
                month_start, next_month_start, _days_in_month = await resolve_season_month_range(
                    conn, region.value, mode.value, season_id
                )
                rank_log_points = await rank_chart.fetch_rank_log_points(
                    conn, bt, region.value, mode.value, season_id
                )
                tracker_points = await _fetch_tracker_points_safe(
                    conn, bt, region.value, mode.value, month_start, next_month_start
                )
                merged = rank_tracker_data.merge_with_rank_log(rank_log_points, tracker_points)
                if not merged:
                    await interaction.followup.send(
                        "❌ No rank data recorded yet this season. Try again later."
                    )
                    return

                mapper = AxisMapper([point for _, point in merged])
                daily = rank_tracker_data.aggregate_by_day(merged, mapper)
                candles = [
                    {"day": day.day, "open": ohlc.open, "close": ohlc.close,
                     "low": ohlc.low, "high": ohlc.high}
                    for day, ohlc in sorted(daily.items())
                ]
                buf = rank_chart.render_candlestick_chart(
                    battletag, region.value, mode.value, season_id, candles, mapper
                )

            file = discord.File(fp=buf, filename="rcc.png")
            await interaction.followup.send(file=file)
        except RuntimeError as exc:
            log.exception("/rcc: user=%s RuntimeError: %s", interaction.user.id, exc)
            await interaction.followup.send(content=f"⏳ {exc}")
        except Exception:
            log.exception("/rcc failed for user=%s", interaction.user)
            await interaction.followup.send(
                content="❌ Something went wrong generating the chart. Please try again."
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _build_section(self, battletag, region, mode):
        if mode is not None:
            return await self._section_single(battletag, region, mode.value, mode.name)
        return await self._section_default(battletag, region)

    async def _section_single(self, battletag, region, mode, mode_label):
        entry, season_id = await self._fetch_entry(battletag, region, mode)
        async with get_db() as conn:
            season_row = await conn.fetchrow(
                """
                SELECT season_score FROM player_season_score
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                battletag.lower(), region, mode, season_id,
            )
            legend_count = await conn.fetchval(
                """
                SELECT legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                ORDER BY date_utc DESC, updated_at DESC
                LIMIT 1
                """,
                battletag.lower(), region, mode, season_id,
            )

        season_score = f"{season_row['season_score']:.2f}" if season_row else "-"

        header = f"🏆 **{battletag}** — {region}  ·  Season {season_id}"
        if entry is None:
            rank_str = "_Not found in top ranks this season._"
        else:
            rank_str = f"**#{entry.rank}**"
            if legend_count:
                percent = entry.rank / legend_count * 100
                rank_str += f" ({percent:.0f}%)"
            if entry.rating:
                rank_str += f"  (MMR {entry.rating})"
        return (
            f"{header}\n"
            f"{mode_label}  ->  {rank_str}\n"
            f"Season Score: {season_score}"
        )

    async def _section_default(self, battletag, region):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bt = battletag.lower()
        (std_entry, std_season), (wild_entry, wild_season) = await asyncio.gather(
            self._fetch_entry(battletag, region, "standard"),
            self._fetch_entry(battletag, region, "wild"),
        )
        async with get_db() as conn:
            std_season_row = await conn.fetchrow(
                """
                SELECT season_score, days_counted FROM player_season_score
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                bt, region, "standard", std_season,
            )
            std_today_row = await conn.fetchrow(
                """
                SELECT dps, legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3
                  AND season_id = $4 AND date_utc = $5
                """,
                bt, region, "standard", std_season, today,
            )
            std_best_row = await conn.fetchrow(
                """
                SELECT best_rank, legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                ORDER BY best_rank ASC, updated_at DESC
                LIMIT 1
                """,
                bt, region, "standard", std_season,
            )
            std_latest_legend_count = await conn.fetchval(
                """
                SELECT legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                ORDER BY date_utc DESC, updated_at DESC
                LIMIT 1
                """,
                bt, region, "standard", std_season,
            )
            wild_season_row = await conn.fetchrow(
                """
                SELECT season_score, days_counted FROM player_season_score
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                bt, region, "wild", wild_season,
            )
            wild_today_row = await conn.fetchrow(
                """
                SELECT dps, legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3
                  AND season_id = $4 AND date_utc = $5
                """,
                bt, region, "wild", wild_season, today,
            )
            wild_best_row = await conn.fetchrow(
                """
                SELECT best_rank, legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                ORDER BY best_rank ASC, updated_at DESC
                LIMIT 1
                """,
                bt, region, "wild", wild_season,
            )
            wild_latest_legend_count = await conn.fetchval(
                """
                SELECT legend_count FROM player_daily_dps
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                ORDER BY date_utc DESC, updated_at DESC
                LIMIT 1
                """,
                bt, region, "wild", wild_season,
            )

        def _rank_with_count(rank, legend_count):
            if rank is None:
                return "-"
            if legend_count:
                return f"#{rank}/{legend_count}"
            return f"#{rank}"

        def _current(e, legend_count):
            return _rank_with_count(e.rank, legend_count) if e else "-"

        def _best(row):
            if row is None:
                return "-"
            return _rank_with_count(row["best_rank"], row["legend_count"])

        def _score(r):
            return f"{r['season_score']:.2f}" if r else "-"

        def _dps(row):
            return f"{row['dps']:.2f}" if row else "-"

        std_current   = _current(std_entry, std_latest_legend_count)
        wild_current  = _current(wild_entry, wild_latest_legend_count)
        std_best_str  = _best(std_best_row)
        wild_best_str = _best(wild_best_row)
        std_score     = _score(std_season_row)
        wild_score    = _score(wild_season_row)
        std_dps       = _dps(std_today_row)
        wild_dps      = _dps(wild_today_row)

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
    async def _fetch_entry(battletag, region, mode):
        needle = battletag.lower().split("#")[0]
        async with get_db() as conn:
            # Current season is derived from the live leaderboard with month-rollover inference.
            season_id = await resolve_current_season_id(conn, region, mode)
            if season_id is None:
                raise RuntimeError(
                    f"Leaderboard data for {region}/{mode} is not available yet. "
                    "The bot is still loading data in the background — please try again in a few minutes."
                )

            # Primary: most recent tracked observation for this registered player in the current season.
            row = await conn.fetchrow(
                """
                SELECT rank, rating
                FROM player_rank_log
                WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                battletag.lower(), region.upper(), mode.lower(), season_id,
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
