"""
Rank progression chart: queries player_rank_log/player_daily_dps and renders
a PNG line chart showing rank over time for /rankchart and /rcc.

Chart y-values are AxisMapper [0,1] positions, not raw ranks — see
bot/services/rank_scale.py and bot/services/rank_tracker_data.py, which
merge this module's player_rank_log data with rank_tracker_matches
(Bronze-Diamond, all leagues) before it ever reaches these render functions.
"""
import io
import logging
from calendar import monthrange
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

from bot.services.season_id import resolve_season_month

log = logging.getLogger(__name__)

# ── Chart color configuration ────────────────────────────────────────────────
# Colors are hex strings; *_TRANSPARENCY values are integer opacity percentages
# (0 = fully transparent, 100 = fully opaque).
# RANK_LINE_COLOR = "#3b48ff"
# RANK_LINE_TRANSPARENCY = 100

# LEGEND_AREA_COLOR = "#ff9f40"
# LEGEND_AREA_TRANSPARENCY = 35

# AXIS_COLOR = "#33333378"
# AXIS_TRANSPARENCY = 100

# BACKGROUND_COLOR = "#ffffff2f"
# BACKGROUND_TRANSPARENCY = 100

RANK_LINE_COLOR = "#5865F2"       # Discord blurple
RANK_LINE_TRANSPARENCY = 100
LEGEND_AREA_COLOR = "#FAA61A"     # Discord amber
LEGEND_AREA_TRANSPARENCY = 25
AXIS_COLOR = "#B9BBBE"            # Discord's muted text gray
AXIS_TRANSPARENCY = 100
BACKGROUND_COLOR = "#313338"      # Discord dark-theme chat background
BACKGROUND_TRANSPARENCY = 100     # or set to 0 for a transparent PNG that blends into any theme

# Candlestick colors (/rcc). "Bullish"/"bearish" are relative to rank, not price:
# a day is bullish (rank improved) when the closing rank is numerically LOWER than
# the opening rank, since lower rank is better.
BULLISH_COLOR = "#23A55A"         # Discord green — rank improved during the day
BULLISH_TRANSPARENCY = 100
BEARISH_COLOR = "#F23F42"         # Discord red — rank worsened during the day
BEARISH_TRANSPARENCY = 100
DOJI_COLOR = "#949BA4"            # open == close (no net change)
DOJI_TRANSPARENCY = 100




def _rgba(hex_color, transparency_pct):
    return mcolors.to_rgba(hex_color, alpha=max(0, min(100, transparency_pct)) / 100)


def _style_axes(fig, *axes):
    bg = _rgba(BACKGROUND_COLOR, BACKGROUND_TRANSPARENCY)
    axis_color = _rgba(AXIS_COLOR, AXIS_TRANSPARENCY)
    fig.patch.set_facecolor(bg)
    for ax in axes:
        ax.set_facecolor(bg)
        ax.tick_params(colors=axis_color)
        ax.xaxis.label.set_color(axis_color)
        for spine in ax.spines.values():
            spine.set_color(axis_color)


async def resolve_days_in_month(conn, region, mode, season_id):
    """
    Days in the calendar month a season falls in (seasons are calendar months).
    Thin wrapper over season_id.resolve_season_month, which derives the month
    from the global ldb_refresh_log table rather than a specific player's data —
    so this works even for a player with zero Legend observations that season.
    """
    month_start = await resolve_season_month(conn, region, mode, season_id)
    return monthrange(month_start.year, month_start.month)[1]


async def fetch_season_legend_counts(conn, battletag, region, mode, season_id, days_in_month):
    """One (day, legend_count) point per day for the season."""
    rows = await conn.fetch(
        """
        SELECT date_utc, legend_count
        FROM player_daily_dps
        WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
          AND legend_count IS NOT NULL
        ORDER BY date_utc
        """,
        battletag, region, mode, season_id,
    )
    by_day = {}
    for row in rows:
        day = datetime.strptime(row["date_utc"], "%Y-%m-%d").day
        by_day[day] = row["legend_count"]

    days = list(range(1, days_in_month + 1))
    counts = [by_day.get(d) for d in days]
    return days, counts


async def fetch_today_series(conn, battletag, region, mode, season_id):
    """Every raw (observed_at, rank) observation from today, no aggregation."""
    rows = await conn.fetch(
        """
        SELECT observed_at, rank
        FROM player_rank_log
        WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
          AND (observed_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
        ORDER BY observed_at
        """,
        battletag, region, mode, season_id,
    )
    times = [row["observed_at"] for row in rows]
    ranks = [row["rank"] for row in rows]
    return times, ranks


async def fetch_rank_log_points(conn, battletag, region, mode, season_id):
    """
    Every raw (observed_at, rank) observation for the whole season, no
    aggregation — the season-wide counterpart to fetch_today_series's
    single-day raw fetch. Feeds rank_tracker_data.merge_with_rank_log, which
    needs real observation timestamps (not one pre-aggregated point per day)
    to interleave correctly with rank_tracker_matches' per-match points.
    """
    rows = await conn.fetch(
        """
        SELECT observed_at, rank
        FROM player_rank_log
        WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
        ORDER BY observed_at
        """,
        battletag, region, mode, season_id,
    )
    return [(row["observed_at"], row["rank"]) for row in rows]


async def fetch_today_legend_count(conn, battletag, region, mode, season_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return await conn.fetchval(
        """
        SELECT legend_count FROM player_daily_dps
        WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4 AND date_utc = $5
        """,
        battletag, region, mode, season_id, today,
    )


def render_season_chart(battletag, region, mode, season_id, rank_type, days, positions, mapper, legend_counts):
    """
    `positions` are AxisMapper [0,1] values (one per day 1..len(days), None for
    days with no data), not raw ranks — see rank_tracker_data.aggregate_by_day.
    `mapper` supplies the tick-label formatter (raw position -> "Legend #123" /
    "Platinum 5").
    """
    fig, ax1 = plt.subplots(figsize=(9, 5))
    rank_color = _rgba(RANK_LINE_COLOR, RANK_LINE_TRANSPARENCY)

    ax1.plot(days, positions, marker="o", markersize=4, color=rank_color, label="Rank")
    ax1.set_ylim(0, 1)
    ax1.invert_yaxis()
    ax1.yaxis.set_major_formatter(mapper.formatter())
    ax1.set_xlim(1, max(days))
    ax1.set_xlabel("Season Day")
    ax1.set_ylabel("Rank (lower is better)", color=rank_color)
    ax1.grid(True, alpha=0.3)

    axes = [ax1]
    if any(c is not None for c in legend_counts):
        ax2 = ax1.twinx()
        legend_color = _rgba(LEGEND_AREA_COLOR, LEGEND_AREA_TRANSPARENCY)
        counts = np.array([c if c is not None else np.nan for c in legend_counts], dtype=float)
        ax2.fill_between(days, counts, color=legend_color, label="Legend Players")
        ax2.set_ylim(bottom=0)
        ax2.set_ylabel("Legend Players", color=legend_color)
        axes.append(ax2)

        # twinx() draws ax2 above ax1 by default; flip so the rank line stays on top.
        ax1.set_zorder(ax2.get_zorder() + 1)
        ax1.patch.set_visible(False)

    rank_type_label = "Best" if rank_type == "best" else "Last"
    fig.suptitle(f"{battletag} — {mode.title()} ({region}) · Season {season_id}", color=AXIS_COLOR)
    ax1.set_title(f"{rank_type_label} rank per day", fontsize=10, color=AXIS_COLOR)

    _style_axes(fig, *axes)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def render_today_chart(battletag, region, mode, season_id, times, positions, mapper, legend_count):
    """`positions`/`mapper`: see render_season_chart's docstring."""
    fig, ax1 = plt.subplots(figsize=(9, 5))
    rank_color = _rgba(RANK_LINE_COLOR, RANK_LINE_TRANSPARENCY)

    ax1.plot(times, positions, marker="o", markersize=4, color=rank_color, label="Rank")
    ax1.set_ylim(0, 1)
    ax1.invert_yaxis()
    ax1.yaxis.set_major_formatter(mapper.formatter())
    ax1.set_xlabel("Time (UTC)")
    ax1.set_ylabel("Rank (lower is better)", color=rank_color)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax1.grid(True, alpha=0.3)

    axes = [ax1]
    if legend_count is not None:
        ax2 = ax1.twinx()
        legend_color = _rgba(LEGEND_AREA_COLOR, LEGEND_AREA_TRANSPARENCY)
        ax2.fill_between(times, [legend_count] * len(times), color=legend_color, label="Legend Players")
        ax2.set_ylim(bottom=0)
        ax2.set_ylabel("Legend Players", color=legend_color)
        axes.append(ax2)

        # twinx() draws ax2 above ax1 by default; flip so the rank line stays on top.
        ax1.set_zorder(ax2.get_zorder() + 1)
        ax1.patch.set_visible(False)

    fig.suptitle(f"{battletag} — {mode.title()} ({region}) · Season {season_id}", color=AXIS_COLOR)
    ax1.set_title("Today's rank", fontsize=10, color=AXIS_COLOR)

    _style_axes(fig, *axes)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


_CANDLE_MIN_BODY_HEIGHT = 0.01  # position-space (uniform [0,1] regardless of Legend/sub-Legend/mixed)


def render_candlestick_chart(battletag, region, mode, season_id, candles, mapper):
    """
    One candle per entry in `candles` (see rank_tracker_data.aggregate_by_day),
    plotted at consecutive x positions labeled with their actual day-of-month —
    days with no data are simply absent from `candles`, so no gaps appear on
    the x-axis. `candles` values (open/close/low/high) are AxisMapper [0,1]
    positions, not raw ranks. The minimum candle body height is now a fixed
    fraction of the [0,1] axis rather than derived from the data's raw range —
    positions are already uniformly scaled regardless of whether a day was
    Legend, sub-Legend, or (the day Legend was first reached) both, so a fixed
    epsilon keeps candle bodies visually proportionate across all three cases.
    """
    fig, ax1 = plt.subplots(figsize=(10, 5))
    axis_color = _rgba(AXIS_COLOR, AXIS_TRANSPARENCY)

    body_width = 0.6

    for i, c in enumerate(candles):
        open_p, close_p = c["open"], c["close"]
        if close_p < open_p:
            color = _rgba(BULLISH_COLOR, BULLISH_TRANSPARENCY)   # rank improved
        elif close_p > open_p:
            color = _rgba(BEARISH_COLOR, BEARISH_TRANSPARENCY)   # rank worsened
        else:
            color = _rgba(DOJI_COLOR, DOJI_TRANSPARENCY)

        ax1.plot([i, i], [c["low"], c["high"]], color=color, linewidth=1, zorder=2)
        body_bottom = min(open_p, close_p)
        body_height = max(abs(close_p - open_p), _CANDLE_MIN_BODY_HEIGHT)
        ax1.add_patch(Rectangle(
            (i - body_width / 2, body_bottom), body_width, body_height,
            facecolor=color, edgecolor=color, zorder=3,
        ))

    ax1.set_xlim(-0.5, len(candles) - 0.5)
    ax1.set_ylim(0, 1)
    ax1.invert_yaxis()
    ax1.yaxis.set_major_formatter(mapper.formatter())
    ax1.set_xticks(range(len(candles)))
    ax1.set_xticklabels([str(c["day"]) for c in candles])
    ax1.set_xlabel("Season Day")
    ax1.set_ylabel("Rank (lower is better)", color=axis_color)
    ax1.grid(True, alpha=0.3, axis="y")

    fig.suptitle(f"{battletag} — {mode.title()} ({region}) · Season {season_id}", color=AXIS_COLOR)
    ax1.set_title("Daily rank — open / close / best / worst", fontsize=10, color=AXIS_COLOR)

    _style_axes(fig, ax1)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
