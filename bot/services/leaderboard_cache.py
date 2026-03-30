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
"""
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from bot.services.db import get_db
from bot.services.leaderboard_client import (
    LeaderboardEntry,
    fetch_leaderboard,
)
from bot.services.season_score import recalculate_season_score

log = logging.getLogger(__name__)


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

    Also tracks registered players: writes a player_rank_log row whenever a
    registered battletag appears in a page, and upserts player_daily_best with
    the best rank seen so far today (UTC).

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
        # Map: battletag_lower (name only, no #NNNN) → discord_id
        registered: dict[str, str] = {
            row["battletag"].lower().split("#")[0]: row["discord_id"]
            for row in await conn.fetch(
                "SELECT discord_id, battletag FROM user_battletags WHERE region = $1",
                region.upper(),
            )
        }

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
            if not registered:
                return
            for entry in page_entries:
                discord_id = registered.get(entry.battletag)
                if discord_id is None:
                    continue
                log.debug(
                    "Tracked registered player %s at rank #%d (%s/%s)",
                    entry.battletag_orig, entry.rank, region, mode,
                )
                await conn.execute(
                    """
                    INSERT INTO player_rank_log
                        (discord_id, region, mode, season_id, rank, rating, observed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    discord_id, region.upper(), mode.lower(),
                    current_season_id, entry.rank, entry.rating, now,
                )
                await conn.execute(
                    """
                    INSERT INTO player_daily_best
                        (discord_id, region, mode, season_id, date_utc, best_rank, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (discord_id, region, mode, season_id, date_utc) DO UPDATE SET
                        best_rank  = LEAST(player_daily_best.best_rank, EXCLUDED.best_rank),
                        updated_at = CASE
                            WHEN EXCLUDED.best_rank < player_daily_best.best_rank
                            THEN EXCLUDED.updated_at
                            ELSE player_daily_best.updated_at
                        END
                    """,
                    discord_id, region.upper(), mode.lower(),
                    current_season_id, date_utc, entry.rank, now,
                )

                legend_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM ldb_current_entries "
                    "WHERE region = $1 AND mode = $2 AND season_id = $3",
                    region.upper(), mode.lower(), current_season_id,
                )
                best_rank = entry.rank
                dps = (
                    math.log10(legend_count) * ((legend_count - best_rank + 1) / legend_count) * 100
                    if legend_count > 0 else 0.0
                )
                await conn.execute(
                    """
                    INSERT INTO player_daily_dps
                        (discord_id, region, mode, season_id, date_utc, dps, best_rank, legend_count, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (discord_id, region, mode, season_id, date_utc) DO UPDATE SET
                        dps          = EXCLUDED.dps,
                        best_rank    = EXCLUDED.best_rank,
                        legend_count = EXCLUDED.legend_count,
                        updated_at   = EXCLUDED.updated_at
                    """,
                    discord_id, region.upper(), mode.lower(),
                    current_season_id, date_utc, dps, best_rank, legend_count, now,
                )

                await recalculate_season_score(
                    discord_id, region.upper(), mode.lower(), current_season_id
                )

        async def on_page_error(page: int) -> None:
            log.warning(
                "%s/%s page %d failed permanently — existing rows kept from previous run",
                region, mode, page,
            )

        _, season_id = await fetch_leaderboard(
            region, mode,
            on_started=on_started,
            on_page=on_page,
            on_page_error=on_page_error,
            max_page=max_page,
        )

        # ── Write refresh audit log ───────────────────────────────────────────
        legend_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ldb_current_entries WHERE region = $1 AND mode = $2",
            region.upper(), mode.lower(),
        )
        await conn.execute(
            """
            INSERT INTO ldb_refresh_log
                (region, mode, season_id, legend_count, is_full, completed_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            region.upper(), mode.lower(), current_season_id,
            legend_count, max_page is None,
            datetime.now(timezone.utc),
        )

    log.info(
        "refresh_pages %s/%s max_page=%s — upserted %d rows (season %s)",
        region, mode, max_page or "all", rows_written, season_id,
    )
    return rows_written, season_id, fetched_at


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
