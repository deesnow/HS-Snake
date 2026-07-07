"""
Rank progression chart: queries player_rank_log/player_daily_dps and renders
a PNG line chart showing rank over time for /rankchart.
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

log = logging.getLogger(__name__)

# ── Chart color configuration ────────────────────────────────────────────────
# Colors are hex strings; *_TRANSPARENCY values are integer opacity percentages
# (0 = fully transparent, 100 = fully opaque).

RANK_LINE_COLOR = "#3b48ff"
RANK_LINE_TRANSPARENCY = 100

LEGEND_AREA_COLOR = "#ff9f40"
LEGEND_AREA_TRANSPARENCY = 35

AXIS_COLOR = "#33333378"
AXIS_TRANSPARENCY = 100

BACKGROUND_COLOR = "#ffffff2f"
BACKGROUND_TRANSPARENCY = 100


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
    Days in the calendar month a season falls in, derived from that season's own
    data (seasons are calendar months). Works for the current season as well as
    any previous/explicit season_id. Falls back to the current month only when a
    season has no data yet (e.g. right after rollover, before any observation).
    """
    max_date = await conn.fetchval(
        """
        SELECT MAX((observed_at AT TIME ZONE 'UTC')::date)
        FROM player_rank_log
        WHERE region = $1 AND mode = $2 AND season_id = $3
        """,
        region, mode, season_id,
    )
    if max_date is None:
        max_date = datetime.now(timezone.utc).date()
    return monthrange(max_date.year, max_date.month)[1]


async def fetch_season_series(conn, battletag, region, mode, season_id, rank_type, days_in_month):
    """One (day, rank) point per day for the season, 1..days_in_month."""
    if rank_type == "best":
        rows = await conn.fetch(
            """
            SELECT (observed_at AT TIME ZONE 'UTC')::date AS day, MIN(rank) AS rank
            FROM player_rank_log
            WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
            GROUP BY day ORDER BY day
            """,
            battletag, region, mode, season_id,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON ((observed_at AT TIME ZONE 'UTC')::date)
                   (observed_at AT TIME ZONE 'UTC')::date AS day, rank
            FROM player_rank_log
            WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
            ORDER BY (observed_at AT TIME ZONE 'UTC')::date, observed_at DESC
            """,
            battletag, region, mode, season_id,
        )

    by_day = {row["day"].day: row["rank"] for row in rows}
    days = list(range(1, days_in_month + 1))
    ranks = [by_day.get(d) for d in days]
    return days, ranks


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


async def fetch_today_legend_count(conn, battletag, region, mode, season_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return await conn.fetchval(
        """
        SELECT legend_count FROM player_daily_dps
        WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4 AND date_utc = $5
        """,
        battletag, region, mode, season_id, today,
    )


def _rank_axis_limits(ranks):
    values = [r for r in ranks if r is not None]
    lo, hi = min(values), max(values)
    pad = max(1, round((hi - lo) * 0.15))
    return max(1, lo - pad), hi + pad


def render_season_chart(battletag, region, mode, season_id, rank_type, days, ranks, legend_counts):
    fig, ax1 = plt.subplots(figsize=(9, 5))
    rank_color = _rgba(RANK_LINE_COLOR, RANK_LINE_TRANSPARENCY)

    ax1.plot(days, ranks, marker="o", markersize=4, color=rank_color, label="Rank")
    ax1.invert_yaxis()
    ax1.set_ylim(*_rank_axis_limits(ranks))
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

    rank_type_label = "Best" if rank_type == "best" else "Last"
    fig.suptitle(f"{battletag} — {mode.title()} ({region}) · Season {season_id}")
    ax1.set_title(f"{rank_type_label} rank per day", fontsize=10)

    _style_axes(fig, *axes)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def render_today_chart(battletag, region, mode, season_id, times, ranks, legend_count):
    fig, ax1 = plt.subplots(figsize=(9, 5))
    rank_color = _rgba(RANK_LINE_COLOR, RANK_LINE_TRANSPARENCY)

    ax1.plot(times, ranks, marker="o", markersize=4, color=rank_color, label="Rank")
    ax1.invert_yaxis()
    ax1.set_ylim(*_rank_axis_limits(ranks))
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

    fig.suptitle(f"{battletag} — {mode.title()} ({region}) · Season {season_id}")
    ax1.set_title("Today's rank", fontsize=10)

    _style_axes(fig, *axes)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
