"""
Leaderboard cache layer.

Wraps leaderboard_client with PostgreSQL persistence using a live upsert table
(ldb_current_entries). Each page is written immediately on arrival; no
snapshot promotion needed. Partial data from previous runs is always visible
to user queries and stays valid until overwritten.

Public API:
    lookup(battletag, region, mode)         -> LeaderboardEntry | None
    get_snapshot(region, mode)              -> (entries, season_id, fetched_at)  — DB only
    refresh_pages(region, mode, max_page)   -> (count, season_id, fetched_at)   — API + upsert
    prune_refresh_log()                     -> rows deleted                     — audit-log retention
"""
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from bot.services.db import get_db
from bot.services.leaderboard_client import (
    LeaderboardEntry,
    fetch_leaderboard,
)
from bot.services.season_id import invalidate_current_season_id
from bot.services.season_score import recalculate_season_score

log = logging.getLogger(__name__)

# player_rank_log dedupe: a registered player seen again with the same rank and
# rating is only re-logged once this much time has passed since their last row
# (and always on their first observation of the UTC day), so charts still get
# regular points without a row per player per refresh.
_RANK_LOG_HEARTBEAT = timedelta(hours=1)

# ldb_refresh_log is a troubleshooting-only audit log; season data lives in ldb_seasons.
_REFRESH_LOG_RETENTION = timedelta(days=14)


async def lookup(
    battletag: str,
    region: str,
    mode: str,
) -> Optional[LeaderboardEntry]:
    entries, _, _ = await get_snapshot(region, mode)
    needle = battletag.lower().split("#")[0]
    return next((e for e in entries if e.battletag == needle), None)


async def get_snapshot(
    region: str,
    mode: str,
) -> tuple[list[LeaderboardEntry], int, str]:
    """
    Return (entries, season_id, fetched_at_iso) from ldb_current_entries.

    Always reads from the DB — never calls the API.
    Returns ([], 0, "") if no data has been stored yet.
    """
    async with get_db() as conn:
        rows = await conn.fetch(
            """
            SELECT rank, battletag, battletag_orig, rating, season_id, updated_at
            FROM ldb_current_entries
            WHERE region = $1 AND mode = $2
            ORDER BY rank
            """,
            region.upper(), mode.lower(),
        )

    if not rows:
        return [], 0, ""

    entries = [
        LeaderboardEntry(
            rank=r["rank"],
            battletag=r["battletag"],
            battletag_orig=r["battletag_orig"],
            rating=r["rating"],
        )
        for r in rows
    ]
    season_id = rows[0]["season_id"]
    fetched_at = max(r["updated_at"] for r in rows)
    return entries, season_id, fetched_at


async def refresh_pages(
    region: str,
    mode: str,
    max_page: Optional[int] = None,
) -> tuple[int, int, str]:
    """
    Fetch pages from the Blizzard API and upsert them into ldb_current_entries.

    Also tracks registered players: writes a player_rank_log row when a
    registered battletag appears in a page with a changed rank/rating (or on its
    first sighting of the UTC day, or after _RANK_LOG_HEARTBEAT), upserts
    player_daily_dps with the best rank seen so far today (UTC), and
    recalculates the season score of every player seen, once per run.

    Each run is recorded in ldb_seasons (first/last refresh time of the season)
    and in the ldb_refresh_log audit log.

    Pages are written as they arrive — no staging/promotion step. A failed
    page is skipped and its existing rows remain from the previous run.

    max_page: stop after this page number (None = fetch all pages).

    Called only by background refresh tasks — never by user commands.
    Returns (rows_written, season_id, fetched_at_iso).
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    current_season_id = 0
    rows_written = 0

    async with get_db() as conn:
        # Load all registered battletags for this region once per refresh run.
        # Map: battletag_lower (name only, no #NNNN) → battletag_lower (full, e.g. "player#1234")
        registered: dict[str, str] = {
            row["battletag"].lower().split("#")[0]: row["battletag"].lower()
            for row in await conn.fetch(
                "SELECT battletag FROM user_battletags WHERE region = $1",
                region.upper(),
            )
        }
        # Last player_rank_log row per registered battletag: (rank, rating, observed_at).
        # Seeded from today's rows in on_started, kept current as rows are written.
        last_logged: dict[str, tuple[int, Optional[int], datetime]] = {}
        # Battletags seen this run; their season scores are recalculated at the end.
        touched: set[str] = set()

        async def on_started(season_id: int) -> None:
            nonlocal current_season_id
            if season_id == 0:
                # The API occasionally returns a null/missing seasonId (transient
                # error). Raising here aborts fetch_leaderboard cleanly so the
                # existing ldb_current_entries data is preserved intact.
                raise ValueError(
                    f"API returned season_id=0 for {region}/{mode} "
                    "— aborting refresh to preserve existing data"
                )
            current_season_id = season_id
            # When a new season starts, wipe stale entries from prior season.
            await conn.execute(
                "DELETE FROM ldb_current_entries "
                "WHERE region = $1 AND mode = $2 AND season_id != $3",
                region.upper(), mode.lower(), season_id,
            )
            invalidate_current_season_id(region, mode)

            if registered:
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                for row in await conn.fetch(
                    """
                    SELECT DISTINCT ON (battletag) battletag, rank, rating, observed_at
                    FROM player_rank_log
                    WHERE region = $1 AND mode = $2 AND season_id = $3
                      AND observed_at >= $4
                    ORDER BY battletag, observed_at DESC
                    """,
                    region.upper(), mode.lower(), season_id, today_start,
                ):
                    last_logged[row["battletag"]] = (row["rank"], row["rating"], row["observed_at"])

        def should_log(battletag: str, entry: LeaderboardEntry, now: datetime) -> bool:
            prev = last_logged.get(battletag)
            if prev is None:
                return True
            prev_rank, prev_rating, prev_at = prev
            return (
                prev_rank != entry.rank
                or prev_rating != entry.rating
                or prev_at.date() != now.date()
                or now - prev_at >= _RANK_LOG_HEARTBEAT
            )

        async def on_page(page: int, raw_rows: list[dict]) -> None:
            nonlocal rows_written
            page_entries = _parse_rows(raw_rows)
            if not page_entries:
                return
            now = datetime.now(timezone.utc)
            date_utc = now.strftime("%Y-%m-%d")

            await conn.executemany(
                """
                INSERT INTO ldb_current_entries
                    (region, mode, season_id, rank, battletag, battletag_orig, rating, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (region, mode, rank) DO UPDATE SET
                    season_id      = EXCLUDED.season_id,
                    battletag      = EXCLUDED.battletag,
                    battletag_orig = EXCLUDED.battletag_orig,
                    rating         = EXCLUDED.rating,
                    updated_at     = EXCLUDED.updated_at
                """,
                [
                    (region.upper(), mode.lower(), current_season_id,
                     e.rank, e.battletag, e.battletag_orig, e.rating, now)
                    for e in page_entries
                ],
            )
            rows_written += len(page_entries)
            log.debug("%s/%s page %d — upserted %d rows", region, mode, page, len(page_entries))

            # ── Track registered players found in this page ───────────────────
            matches = [
                (registered[e.battletag], e)
                for e in page_entries
                if e.battletag in registered
            ]
            if not matches:
                return

            legend_count = await conn.fetchval(
                "SELECT COUNT(*) FROM ldb_current_entries "
                "WHERE region = $1 AND mode = $2 AND season_id = $3",
                region.upper(), mode.lower(), current_season_id,
            )

            rank_log_rows = []
            daily_dps_rows = []
            for battletag, entry in matches:
                log.debug(
                    "Tracked registered player %s at rank #%d (%s/%s)",
                    entry.battletag_orig, entry.rank, region, mode,
                )
                if should_log(battletag, entry, now):
                    rank_log_rows.append((
                        battletag, region.upper(), mode.lower(),
                        current_season_id, entry.rank, entry.rating, now,
                    ))
                    last_logged[battletag] = (entry.rank, entry.rating, now)
                best_rank = entry.rank
                dps = (
                    math.log10(legend_count) * ((legend_count - best_rank + 1) / legend_count) * 100
                    if legend_count > 0 else 0.0
                )
                daily_dps_rows.append((
                    battletag, region.upper(), mode.lower(),
                    current_season_id, date_utc, dps, best_rank, legend_count, now,
                ))
                touched.add(battletag)

            if rank_log_rows:
                await conn.executemany(
                    """
                    INSERT INTO player_rank_log
                        (battletag, region, mode, season_id, rank, rating, observed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    rank_log_rows,
                )
            await conn.executemany(
                """
                INSERT INTO player_daily_dps
                    (battletag, region, mode, season_id, date_utc, dps, best_rank, legend_count, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (battletag, region, mode, season_id, date_utc) DO UPDATE SET
                    best_rank    = LEAST(player_daily_dps.best_rank, EXCLUDED.best_rank),
                    dps          = CASE
                        WHEN EXCLUDED.best_rank < player_daily_dps.best_rank
                        THEN EXCLUDED.dps
                        ELSE player_daily_dps.dps
                    END,
                    legend_count = CASE
                        WHEN EXCLUDED.best_rank < player_daily_dps.best_rank
                        THEN EXCLUDED.legend_count
                        ELSE player_daily_dps.legend_count
                    END,
                    updated_at   = CASE
                        WHEN EXCLUDED.best_rank < player_daily_dps.best_rank
                        THEN EXCLUDED.updated_at
                        ELSE player_daily_dps.updated_at
                    END
                """,
                daily_dps_rows,
            )

        async def on_page_error(page: int) -> None:
            log.warning(
                "%s/%s page %d failed permanently — existing rows kept from previous run",
                region, mode, page,
            )

        try:
            _, season_id = await fetch_leaderboard(
                region, mode,
                on_started=on_started,
                on_page=on_page,
                on_page_error=on_page_error,
                max_page=max_page,
            )
        finally:
            # Runs even if the fetch failed mid-way, so daily_dps rows written
            # before the failure are reflected in season scores.
            for battletag in touched:
                try:
                    await recalculate_season_score(
                        conn, battletag, region.upper(), mode.lower(), current_season_id
                    )
                except Exception:
                    log.exception(
                        "Season score recalculation failed for %s %s/%s",
                        battletag, region, mode,
                    )

        # ── Record the run: season table + refresh audit log ──────────────────
        legend_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ldb_current_entries WHERE region = $1 AND mode = $2",
            region.upper(), mode.lower(),
        )
        completed_at = datetime.now(timezone.utc)
        await conn.execute(
            """
            INSERT INTO ldb_seasons
                (region, mode, season_id, first_refresh_at, last_refresh_at, legend_count)
            VALUES ($1, $2, $3, $4, $4, $5)
            ON CONFLICT (region, mode, season_id) DO UPDATE SET
                last_refresh_at = EXCLUDED.last_refresh_at,
                legend_count    = EXCLUDED.legend_count
            """,
            region.upper(), mode.lower(), current_season_id, completed_at, legend_count,
        )
        await conn.execute(
            """
            INSERT INTO ldb_refresh_log
                (region, mode, season_id, legend_count, is_full, completed_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            region.upper(), mode.lower(), current_season_id,
            legend_count, max_page is None,
            completed_at,
        )

    log.info(
        "refresh_pages %s/%s max_page=%s — upserted %d rows (season %s)",
        region, mode, max_page or "all", rows_written, season_id,
    )
    return rows_written, season_id, fetched_at


async def prune_refresh_log() -> int:
    """Delete ldb_refresh_log rows older than _REFRESH_LOG_RETENTION. Returns the count."""
    async with get_db() as conn:
        status = await conn.execute(
            "DELETE FROM ldb_refresh_log WHERE completed_at < $1",
            datetime.now(timezone.utc) - _REFRESH_LOG_RETENTION,
        )
    return int(status.split()[-1])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_rows(rows: list[dict]) -> list[LeaderboardEntry]:
    result = []
    for row in rows:
        bt = row.get("accountid") or ""
        if bt:
            result.append(LeaderboardEntry(
                rank=int(row["rank"]),
                battletag_orig=bt,
                battletag=bt.lower(),
                rating=row.get("rating"),
            ))
    return result
