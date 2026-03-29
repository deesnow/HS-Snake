"""
Service for calculating and updating season scores for each player.
"""
import logging
from datetime import datetime, timezone
from bot.services.db import get_db

log = logging.getLogger(__name__)

async def recalculate_season_score(discord_id: str, region: str, mode: str, season_id: int):
    """
    Recalculate and upsert the season score for a player for the given season.
    Season Score = average of all daily DPS values for the player in the season.
    """
    async with get_db() as db:
        # Find the max day for this season in player_daily_dps (or use today if no data)
        cursor = await db.execute(
            """
            SELECT MAX(date_utc) as max_date FROM player_daily_dps
            WHERE region = ? AND mode = ? AND season_id = ?
            """,
            (region, mode, season_id),
        )
        row = await cursor.fetchone()
        max_date = row["max_date"]
        if not max_date:
            days_counted = 0
            season_score = 0.0
        else:
            from datetime import datetime, timedelta
            d1 = datetime.strptime(max_date, "%Y-%m-%d")
            # min_day is the first day of the month of max_day
            d0 = d1.replace(day=1)
            n_days = (d1 - d0).days + 1
            days_counted = n_days
            # Build a set of all days in the season
            all_days = {(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days)}
            # Get all DPS values for this player
            cursor = await db.execute(
                """
                SELECT date_utc, dps FROM player_daily_dps
                WHERE discord_id = ? AND region = ? AND mode = ? AND season_id = ?
                """,
                (discord_id, region, mode, season_id),
            )
            dps_by_day = {row["date_utc"]: row["dps"] for row in await cursor.fetchall()}
            # For missing days, treat DPS as 0
            total_dps = sum(dps_by_day.get(day, 0.0) for day in all_days)
            season_score = total_dps / days_counted if days_counted > 0 else 0.0
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT INTO player_season_score
                (discord_id, region, mode, season_id, season_score, days_counted, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id, region, mode, season_id) DO UPDATE SET
                season_score = excluded.season_score,
                days_counted = excluded.days_counted,
                updated_at = excluded.updated_at
            """,
            (discord_id, region, mode, season_id, season_score, days_counted, now),
        )
        await db.commit()
        log.info(f"Updated season score for {discord_id} {region}/{mode} season {season_id}: {season_score:.2f} ({days_counted} days)")

async def recalculate_all_season_scores(region: str, mode: str, season_id: int):
    """
    Recalculate season scores for all players in a region/mode/season.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT discord_id FROM player_daily_dps
            WHERE region = ? AND mode = ? AND season_id = ?
            """,
            (region, mode, season_id),
        )
        players = [row["discord_id"] for row in await cursor.fetchall()]
    for discord_id in players:
        await recalculate_season_score(discord_id, region, mode, season_id)
