"""
Queries rank_tracker_matches (decktrackerAPI's per-match, all-league upload
table) and merges it with rank_chart's existing player_rank_log observations
(Legend-only, from the periodic leaderboard scrape) for /rankchart and /rcc.

The merge is a true chronological union, never a day-level "pick one source"
choice: player_rank_log keeps showing real rank movement on days without a
tracked match (e.g. the plugin wasn't running, or a match upload was
dropped), while rank_tracker_matches adds all-league, per-match granularity
wherever it's available. See ToDo.MD Phase 16 / the approved plan for the
rationale (an earlier day-exclusive design was rejected).
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from bot.services.rank_scale import AxisMapper, RankPoint, classify


async def fetch_tracker_points(conn, battletag, region, mode, start, end):
    """
    Two points per match from rank_tracker_matches: pre-match state at
    start_time, post-match state at end_time — so a single ranked match
    renders as a visible step rather than a single post-match dot.

    Only current-system matches (league_id = 5) are included; legacy
    pre-rework rows aren't modeled by rank_scale and aren't expected from
    modern plugin builds (see payload-definition.md). Rows with a NULL
    region (uploaded before the plugin started sending one) are excluded by
    the region filter — an accepted gap, since there's no way to attribute
    them to a region after the fact.
    """
    rows = await conn.fetch(
        """
        SELECT start_time, end_time, star_level, stars, legend_rank,
               star_level_after, stars_after, legend_rank_after
        FROM rank_tracker_matches
        WHERE LOWER(player_battletag) = LOWER($1)
          AND region = $2
          AND game_mode = 'Ranked'
          AND format ILIKE $3
          AND league_id = 5
          AND end_time >= $4 AND end_time < $5
        ORDER BY end_time
        """,
        battletag, region.upper(), mode, start, end,
    )
    # Legend points are returned too: tracker_ingest only folds *today's*
    # matches into player_rank_log, so earlier Legend matches (bot offline,
    # plugin uploaded later) exist only here. merge_with_rank_log drops the ones
    # player_rank_log already holds.
    points = []
    for row in rows:
        points.append((row["start_time"], row["star_level"], row["stars"], row["legend_rank"]))
        points.append((row["end_time"], row["star_level_after"], row["stars_after"], row["legend_rank_after"]))
    return points


def merge_with_rank_log(rank_log_points, tracker_points):
    """
    Chronological union of both sources — never excludes either one.

    `rank_log_points`: [(timestamp, rank), ...] from
    rank_chart.fetch_rank_log_points/fetch_today_series (always Legend, since
    the leaderboard scrape only ever sees Legend players).

    `tracker_points`: [(timestamp, star_level, stars, legend_rank), ...] from
    fetch_tracker_points (any league, via rank_scale.classify).
    """
    merged = [(ts, RankPoint(kind="legend", ordinal=rank)) for ts, rank in rank_log_points]
    logged = set(rank_log_points)  # (timestamp, rank) already present — skip duplicates
    merged += [
        (ts, classify(star_level, stars, legend_rank))
        for ts, star_level, stars, legend_rank in tracker_points
        if not legend_rank or (ts, legend_rank) not in logged
    ]
    merged.sort(key=lambda item: item[0])
    return merged


def fill_legend_gaps(
    daily: "dict[date, DayOHLC]",
    merged_points: Sequence[tuple[datetime, RankPoint]],
    last_day: date,
) -> "dict[date, DayOHLC]":
    """
    Forward-fill days with no observation up to `last_day` with a flat candle
    at the previous close — but only while the player is in Legend (their last
    point on the previous observed day was a Legend point). A Legend rank
    unchanged for a day is still a real rank, whereas a missing sub-Legend day
    just means nothing was tracked, so that stays a gap.
    """
    if not daily:
        return daily
    last_kind: dict[date, str] = {}
    for timestamp, point in merged_points:
        last_kind[timestamp.astimezone(timezone.utc).date()] = point.kind

    filled = dict(daily)
    day = min(daily)
    prev: DayOHLC | None = None
    in_legend = False
    while day <= last_day:
        if day in daily:
            prev = daily[day]
            in_legend = last_kind.get(day) == "legend"
        elif prev is not None and in_legend:
            filled[day] = DayOHLC(open=prev.close, close=prev.close, low=prev.close, high=prev.close)
        day += timedelta(days=1)
    return filled


@dataclass(frozen=True)
class DayOHLC:
    """Open/close/low/high in AxisMapper [0,1] position-space for one UTC day."""
    open: float
    close: float
    low: float
    high: float


def aggregate_by_day(
    merged_points: Sequence[tuple[datetime, RankPoint]],
    axis_mapper: AxisMapper,
) -> dict[date, DayOHLC]:
    """
    Per-UTC-day open (first chronologically)/close (last)/low (best,
    i.e. smallest position)/high (worst, largest position).

    Computed in AxisMapper position-space, not raw legend_rank/climb_score
    values — a Legend point and a sub-Legend point aren't directly comparable
    on their raw ordinals (different scales, different meaning), but their
    [0,1] positions always are. `axis_mapper` must be built from the *entire*
    merged stream (not just one day) so single-/mixed-regime scaling
    reflects the whole season — see AxisMapper's docstring.

    `merged_points` must already be chronologically sorted (as returned by
    merge_with_rank_log) — open/close are simply the first/last position
    seen for each day, not separately re-sorted here.
    """
    positions_by_day: dict[date, list[float]] = defaultdict(list)
    for timestamp, point in merged_points:
        day = timestamp.astimezone(timezone.utc).date()
        positions_by_day[day].append(axis_mapper.position(point))

    return {
        day: DayOHLC(
            open=positions[0],
            close=positions[-1],
            low=min(positions),
            high=max(positions),
        )
        for day, positions in positions_by_day.items()
    }
