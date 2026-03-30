"""
Service for calculating and updating season scores for each player.
"""
import logging
from datetime import datetime, timezone, timedelta

from bot.services.db import get_db

log = logging.getLogger(__name__)


async def recalculate_season_score(discord_id: str, region: str, mode: str, season_id: int) -> None:
    """
    Recalculate and upsert the season score for a player for the given season.
    Season Score = average of all daily DPS values for the player in the season.
    """
    async with get_db() as conn:
        max_date = await conn.fetchval(
            """
            SELECT MAX(date_utc) FROM player_daily_dps
            WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
            """,
            discord_id, region, mode, season_id,
        )

        if not max_date:
            days_counted = 0
            season_score = 0.0
        else:
            # d0 = first day of the season (month of first data)
            # d1 = today UTC (season is still running)
            # n = days in season so far — missing days count as DPS 0
            d0 = datetime.strptime(max_date, "%Y-%m-%d").replace(day=1)
            d1 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            n_days = (d1 - d0).days + 1
            days_counted = n_days
            all_days = {(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days)}

            rows = await conn.fetch(
                """
                SELECT date_utc, dps FROM player_daily_dps
                WHERE discord_id = $1 AND region = $2 AND mode = $3 AND season_id = $4
                """,
                discord_id, region, mode, season_id,
            )
            dps_by_day = {r["date_utc"]: r["dps"] for r in rows}
            total_dps = sum(dps_by_day.get(day, 0.0) for day in all_days)
            season_score = total_dps / days_counted

        now = datetime.now(timezone.utc)
        await conn.execute(
            """
            INSERT INTO player_season_score
                (discord_id, region, mode, season_id, season_score, days_counted, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (discord_id, region, mode, season_id) DO UPDATE SET
                season_score = EXCLUDED.season_score,
                days_counted = EXCLUDED.days_counted,
                updated_at   = EXCLUDED.updated_at
            """,
            discord_id, region, mode, season_id, season_score, days_counted, now,
        )

    log.info(
        "Updated season score for %s %s/%s season %s: %.2f (%d days)",
        discord_id, region, mode, season_id, season_score, days_counted,
    )


async def recalculate_all_season_scores(region: str, mode: str, season_id: int) -> None:
    """
    Recalculate season scores for all players in a region/mode/season.
    """
    async with get_db() as conn:
        players = [
            r["discord_id"] for r in await conn.fetch(
                """
                SELECT DISTINCT discord_id FROM player_daily_dps
                WHERE region = $1 AND mode = $2 AND season_id = $3
                """,
                region, mode, season_id,
            )
        ]

    for discord_id in players:
        await recalculate_season_score(discord_id, region, mode, season_id)
