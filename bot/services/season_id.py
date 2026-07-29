"""
Helpers for resolving leaderboard season IDs across month rollover.
"""
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Optional, Tuple


async def resolve_current_season_id(conn, region: str, mode: str) -> Optional[int]:
    """
    Resolve the current season ID for a region/mode.

    Normally this is the max season_id stored in ldb_current_entries. During the
    first two UTC days of a new month, Blizzard may have opened the new season
    before any legend entries or refresh audit rows exist for that month. In
    that rollover window, infer the new current season as max(ldb_current)+1.
    """
    region_value = region.upper()
    mode_value = mode.lower()

    current_season_id = await conn.fetchval(
        "SELECT MAX(season_id) FROM ldb_current_entries WHERE region = $1 AND mode = $2",
        region_value,
        mode_value,
    )
    if current_season_id is None:
        return None

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    has_current_month_entries = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM ldb_current_entries
            WHERE region = $1 AND mode = $2 AND updated_at >= $3
        )
        """,
        region_value,
        mode_value,
        month_start,
    )
    if has_current_month_entries:
        return current_season_id

    has_current_month_refresh = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM ldb_refresh_log
            WHERE region = $1 AND mode = $2 AND completed_at >= $3
        )
        """,
        region_value,
        mode_value,
        month_start,
    )
    if has_current_month_refresh:
        return current_season_id

    if now.day <= 2:
        return current_season_id + 1

    return current_season_id


async def resolve_season_month(conn, region: str, mode: str, season_id: int) -> date:
    """
    Calendar month (as its first-of-month date) a season_id falls in.

    Derived from ldb_refresh_log — the append-only refresh audit log, keyed only
    by region+mode+season_id, never battletag. This works for ANY season_id,
    including ones a specific player has zero data for (e.g. they never reached
    Legend that month), unlike deriving the month from a player's own
    player_rank_log rows. It also works for past seasons, unlike
    ldb_current_entries, which is a live-upsert table that only ever holds the
    *current* season's rows and loses history on every rollover.

    Falls back to the current month if the season has no refresh-log rows yet
    (e.g. immediately after rollover, before the first refresh cycle completes).
    """
    region_value = region.upper()
    mode_value = mode.lower()

    completed_at = await conn.fetchval(
        """
        SELECT MIN(completed_at) FROM ldb_refresh_log
        WHERE region = $1 AND mode = $2 AND season_id = $3
        """,
        region_value, mode_value, season_id,
    )
    if completed_at is None:
        completed_at = datetime.now(timezone.utc)

    return completed_at.date().replace(day=1)


async def resolve_season_month_range(
    conn, region: str, mode: str, season_id: int
) -> Tuple[datetime, datetime, int]:
    """
    (month_start, next_month_start, days_in_month) for a season, as UTC-aware
    datetimes suitable for TIMESTAMPTZ range filters (e.g. rank_tracker_matches
    queries: `end_time >= month_start AND end_time < next_month_start`).

    Built on resolve_season_month, so it shares the same guarantees (works for
    any season_id, including ones a given player has zero data for, and past
    seasons — not just the current one).
    """
    month_start_date = await resolve_season_month(conn, region, mode, season_id)
    days_in_month = monthrange(month_start_date.year, month_start_date.month)[1]
    month_start = datetime(month_start_date.year, month_start_date.month, 1, tzinfo=timezone.utc)
    if month_start_date.month == 12:
        next_month_start = datetime(month_start_date.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month_start = datetime(month_start_date.year, month_start_date.month + 1, 1, tzinfo=timezone.utc)
    return month_start, next_month_start, days_in_month


async def resolve_season_id_by_arg(conn, region: str, mode: str, season_raw: str) -> Optional[int]:
    """
    Resolve a season_id from a normalized `season` command argument.

    season_raw must already be normalized/validated to one of: "current", "previous",
    or a string containing an explicit season number (see parse_season_arg).
    Returns None if there's no leaderboard data yet to resolve "current"/"previous" from.
    """
    if season_raw == "previous":
        current_season_id = await resolve_current_season_id(conn, region, mode)
        return None if current_season_id is None else current_season_id - 1
    if season_raw == "current":
        return await resolve_current_season_id(conn, region, mode)
    return int(season_raw)


def parse_season_arg(season: Optional[str]) -> Optional[str]:
    """
    Normalize a raw `season` command option to "current", "previous", or a numeric
    string, for use with resolve_season_id_by_arg. Returns None if the input is
    non-numeric and isn't "current"/"previous" (i.e. invalid).
    """
    season_raw = (season or "current").strip().lower()
    if season_raw in ("current", "previous"):
        return season_raw
    try:
        int(season_raw)
    except ValueError:
        return None
    return season_raw
