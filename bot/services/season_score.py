"""
Service for calculating and updating season scores for each player.

Functions take the caller's connection rather than acquiring their own: they
run inside leaderboard refreshes that already hold a pool connection, and a
nested acquire there doubles pool usage per refresh.
"""
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


async def recalculate_season_score(conn, battletag: str, region: str, mode: str, season_id: int) -> None:
    """
    Recalculate and upsert the season score for a player for the given season.
    Season Score = average of all daily DPS values for the player in the season.
    """
    rows = await conn.fetch(
        """
        SELECT date_utc, dps FROM player_daily_dps
        WHERE battletag = $1 AND region = $2 AND mode = $3 AND season_id = $4
        """,
        battletag, region, mode, season_id,
    )

    if not rows:
        days_counted = 0
        season_score = 0.0
    else:
        # d0 = first day of the season (month of latest data)
        # d1 = today UTC (season is still running)
        # n = days in season so far — missing days count as DPS 0
        max_date = max(r["date_utc"] for r in rows)
        if isinstance(max_date, str):
            d0 = datetime.strptime(max_date, "%Y-%m-%d").replace(day=1, tzinfo=timezone.utc)
        else:
            d0 = max_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        d1 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        n_days = (d1 - d0).days + 1
        days_counted = n_days
        all_days = {(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days)}

        dps_by_day = {r["date_utc"]: r["dps"] for r in rows}
        total_dps = sum(dps_by_day.get(day, 0.0) for day in all_days)
        season_score = total_dps / days_counted

    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        INSERT INTO player_season_score
            (battletag, region, mode, season_id, season_score, days_counted, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (battletag, region, mode, season_id) DO UPDATE SET
            season_score = EXCLUDED.season_score,
            days_counted = EXCLUDED.days_counted,
            updated_at   = EXCLUDED.updated_at
        """,
        battletag, region, mode, season_id, season_score, days_counted, now,
    )

    log.debug(
        "Updated season score for %s %s/%s season %s: %.2f (%d days)",
        battletag, region, mode, season_id, season_score, days_counted,
    )


async def recalculate_all_season_scores(conn, region: str, mode: str, season_id: int) -> None:
    """
    Recalculate season scores for all players in a region/mode/season.
    """
    players = [
        r["battletag"] for r in await conn.fetch(
            """
            SELECT DISTINCT battletag FROM player_daily_dps
            WHERE region = $1 AND mode = $2 AND season_id = $3
            """,
            region, mode, season_id,
        )
    ]

    for battletag in players:
        await recalculate_season_score(conn, battletag, region, mode, season_id)
