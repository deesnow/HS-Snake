"""
Feeds plugin uploads (rank_tracker_matches) into the same tables the
leaderboard refresh maintains for registered players: player_rank_log,
player_daily_dps and player_season_score. That makes /rank, season score and
today's score reflect a match within seconds of the upload instead of waiting
for the next leaderboard scrape.

Idempotent: a match is identified by (battletag, region, mode, observed_at =
match end_time) in player_rank_log, so it can run on a short loop over
"today's matches" without double-writing. The leaderboard scrape remains
authoritative — whichever observation is newer wins in /rank.
"""
import logging
import math
from datetime import datetime, timezone

from bot.services.db import get_db
from bot.services.season_id import resolve_current_season_id
from bot.services.season_score import recalculate_season_score

log = logging.getLogger(__name__)


async def ingest_recent_matches() -> int:
    """Ingest today's (UTC) Legend ranked matches of registered players. Returns rows logged."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    date_utc = today_start.strftime("%Y-%m-%d")

    async with get_db() as conn:
        matches = await conn.fetch(
            """
            SELECT u.region, LOWER(u.battletag) AS battletag, LOWER(m.format) AS mode,
                   m.end_time, m.legend_rank_after
            FROM rank_tracker_matches m
            JOIN user_battletags u
              ON u.region = m.region AND LOWER(u.battletag) = LOWER(m.player_battletag)
            WHERE m.game_mode = 'Ranked'
              AND m.league_id = 5
              AND m.legend_rank_after > 0
              AND LOWER(m.format) IN ('standard', 'wild')
              AND m.end_time >= $1
            ORDER BY m.end_time
            """,
            today_start,
        )
        if not matches:
            return 0

        logged = {
            (r["battletag"], r["region"], r["mode"], r["observed_at"])
            for r in await conn.fetch(
                "SELECT battletag, region, mode, observed_at FROM player_rank_log "
                "WHERE observed_at >= $1",
                today_start,
            )
        }

        season_ids: dict[tuple[str, str], int | None] = {}
        legend_counts: dict[tuple[str, str], int] = {}
        best_today: dict[tuple[str, str, str], int] = {}
        log_rows = []
        for m in matches:
            rm = (m["region"], m["mode"])
            if rm not in season_ids:
                season_ids[rm] = await resolve_current_season_id(conn, *rm)
                legend_counts[rm] = await conn.fetchval(
                    "SELECT COUNT(*) FROM ldb_current_entries WHERE region = $1 AND mode = $2",
                    *rm,
                ) or 0
            season_id = season_ids[rm]
            if season_id is None:
                continue
            rank = m["legend_rank_after"]
            key = (m["battletag"], *rm)
            best_today[key] = min(best_today.get(key, rank), rank)
            if (m["battletag"], m["region"], m["mode"], m["end_time"]) not in logged:
                log_rows.append((m["battletag"], m["region"], m["mode"], season_id, rank, None, m["end_time"]))

        if log_rows:
            await conn.executemany(
                """
                INSERT INTO player_rank_log (battletag, region, mode, season_id, rank, rating, observed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                log_rows,
            )

        for (battletag, region, mode), best_rank in best_today.items():
            season_id = season_ids[(region, mode)]
            legend_count = legend_counts[(region, mode)]
            # Same formula as leaderboard_cache.refresh_pages. A rank beyond the cached
            # legend_count (stale scrape) would give a negative score — skip until it catches up.
            if legend_count <= 0 or best_rank > legend_count:
                continue
            dps = math.log10(legend_count) * ((legend_count - best_rank + 1) / legend_count) * 100
            await conn.execute(
                """
                INSERT INTO player_daily_dps
                    (battletag, region, mode, season_id, date_utc, dps, best_rank, legend_count, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (battletag, region, mode, season_id, date_utc) DO UPDATE SET
                    best_rank    = LEAST(player_daily_dps.best_rank, EXCLUDED.best_rank),
                    dps          = CASE WHEN EXCLUDED.best_rank < player_daily_dps.best_rank
                                        THEN EXCLUDED.dps ELSE player_daily_dps.dps END,
                    legend_count = CASE WHEN EXCLUDED.best_rank < player_daily_dps.best_rank
                                        THEN EXCLUDED.legend_count ELSE player_daily_dps.legend_count END,
                    updated_at   = CASE WHEN EXCLUDED.best_rank < player_daily_dps.best_rank
                                        THEN EXCLUDED.updated_at ELSE player_daily_dps.updated_at END
                """,
                battletag, region, mode, season_id, date_utc, dps, best_rank, legend_count, now,
            )
            await recalculate_season_score(conn, battletag, region, mode, season_id)

    if log_rows:
        log.info("Tracker ingest: logged %d new match observation(s)", len(log_rows))
    return len(log_rows)
