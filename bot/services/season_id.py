"""
Helpers for resolving leaderboard season IDs across month rollover.
"""
from datetime import datetime, timezone
from typing import Optional


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
