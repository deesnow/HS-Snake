"""
Leaderboard cache layer.

Wraps leaderboard_client with SQLite persistence using a live upsert table
(ldb_current_entries). Each page is written immediately on arrival; no
snapshot promotion needed. Partial data from previous runs is always visible
to user queries and stays valid until overwritten.

Public API:
    lookup(battletag, region, mode)         -> LeaderboardEntry | None
    get_snapshot(region, mode)              -> (entries, season_id, fetched_at)  — DB only
    refresh_pages(region, mode, max_page)   -> (count, season_id, fetched_at)   — API + upsert
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.services.db import get_db
from bot.services.leaderboard_client import (
    LeaderboardEntry,
    fetch_leaderboard,
)

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
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT rank, battletag, battletag_orig, rating, season_id, updated_at
            FROM ldb_current_entries
            WHERE region = ? AND mode = ?
            ORDER BY rank
            """,
            (region.upper(), mode.lower()),
        )
        rows = await cursor.fetchall()

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

    Pages are written as they arrive — no staging/promotion step. A failed
    page is skipped and its existing rows remain from the previous run.

    max_page: stop after this page number (None = fetch all pages).

    Called only by background refresh tasks — never by user commands.
    Returns (rows_written, season_id, fetched_at_iso).
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    current_season_id = 0
    rows_written = 0

    async with get_db() as db:

        async def on_started(season_id: int) -> None:
            nonlocal current_season_id
            current_season_id = season_id
            # When a new season starts, wipe stale entries from prior season.
            await db.execute(
                "DELETE FROM ldb_current_entries "
                "WHERE region = ? AND mode = ? AND season_id != ?",
                (region.upper(), mode.lower(), season_id),
            )
            await db.commit()

        async def on_page(page: int, raw_rows: list[dict]) -> None:
            nonlocal rows_written
            page_entries = _parse_rows(raw_rows)
            if not page_entries:
                return
            now = datetime.now(timezone.utc).isoformat()
            await db.executemany(
                """
                INSERT INTO ldb_current_entries
                    (region, mode, season_id, rank, battletag, battletag_orig, rating, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(region, mode, rank) DO UPDATE SET
                    season_id      = excluded.season_id,
                    battletag      = excluded.battletag,
                    battletag_orig = excluded.battletag_orig,
                    rating         = excluded.rating,
                    updated_at     = excluded.updated_at
                """,
                [
                    (region.upper(), mode.lower(), current_season_id,
                     e.rank, e.battletag, e.battletag_orig, e.rating, now)
                    for e in page_entries
                ],
            )
            await db.commit()
            rows_written += len(page_entries)
            log.debug("%s/%s page %d — upserted %d rows", region, mode, page, len(page_entries))

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

