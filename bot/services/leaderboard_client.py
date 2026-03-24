"""
Blizzard Hearthstone leaderboard API client.

Fetches all pages for a (region, mode) combination and returns a flat list
of (rank, battletag_orig, rating) tuples. No auth required — public endpoint.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class _RateLimiter:
    """
    Token-bucket rate limiter with adaptive backoff.

    On 4xx the interval doubles (capped at 5 s/request) so all queued tasks
    automatically slow down together. Each successful slot recovers 10% toward
    the configured baseline.
    """

    def __init__(self, rate: float) -> None:
        self._min_interval = 1.0 / rate
        self._interval = self._min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    def throttle(self) -> None:
        """Called on 4xx — doubles the per-request interval, max 5 s."""
        self._interval = min(self._interval * 2.0, 5.0)

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            gap = self._last + self._interval - now
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = asyncio.get_event_loop().time()
            # Gradually recover to baseline after each successful slot
            if self._interval > self._min_interval:
                self._interval = max(self._interval * 0.9, self._min_interval)


# Shared across ALL fetch_leaderboard calls (including parallel combos).
# 3 req/s ≈ 180 req/min — stays below Blizzard's observed ~200 req/min limit.
_rate_limiter = _RateLimiter(rate=3.0)

# Pause durations after consecutive 4xx — long enough for the API's
# per-minute sliding window to recover.
_RETRY_WAITS = (30, 60, 120)  # seconds

_LDB_URL = (
    "https://hearthstone.blizzard.com/en-us/api/community/leaderboardsData"
    "?region={region}&leaderboardId={mode}&page={page}"
)

VALID_REGIONS = {"EU", "US", "AP"}
VALID_MODES = {
    "standard", "wild", "classic",
    "battlegrounds", "battlegroundsduo",
    "arena", "twist",
}


@dataclass
class LeaderboardEntry:
    rank: int
    battletag_orig: str   # original casing from API
    battletag: str        # lower-cased for lookup
    rating: Optional[int]


async def fetch_leaderboard(
    region: str,
    mode: str,
    *,
    http: Optional[httpx.AsyncClient] = None,
    on_started: Optional[Callable[[int], Awaitable[None]]] = None,
    on_page: Optional[Callable[[int, list[dict]], Awaitable[None]]] = None,
    on_page_error: Optional[Callable[[int], Awaitable[None]]] = None,
    max_page: Optional[int] = None,
) -> tuple[list[LeaderboardEntry], int]:
    """
    Fetch pages for (region, mode), up to max_page if given.

    Returns (entries, season_id).
    Raises httpx.HTTPError on network failure.

    Optional async callbacks:
      on_started(season_id)         — after page 1 parsed, before any on_page calls
      on_page(page_num, raw_rows)   — every successfully fetched page (incl. page 1)
      on_page_error(page_num)       — when all retries for a page are exhausted;
                                       if provided the page is skipped rather than raising
    """
    own_client = http is None
    if own_client:
        http = httpx.AsyncClient(timeout=30.0, follow_redirects=True, limits=httpx.Limits(max_connections=5, max_keepalive_connections=5))

    entries: list[LeaderboardEntry] = []
    season_id = 0

    try:
        # Page 1 — discover total_pages and season_id
        await _rate_limiter.acquire()
        url = _LDB_URL.format(region=region, mode=mode, page=1)
        log.debug("GET %s", url)
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.json()

        season_id = data.get("seasonId", 0)
        total_pages = data["leaderboard"]["pagination"]["totalPages"]
        page1_rows = data["leaderboard"]["rows"]

        log.info(
            "Leaderboard %s/%s season=%s pages=%d",
            region, mode, season_id, total_pages,
        )

        if on_started is not None:
            await on_started(season_id)
        if on_page is not None:
            await on_page(1, page1_rows)
        _append_rows(entries, page1_rows)

        async def _fetch_page(page: int) -> list[dict]:
            u = _LDB_URL.format(region=region, mode=mode, page=page)
            for attempt in range(len(_RETRY_WAITS) + 1):
                await _rate_limiter.acquire()
                log.debug("GET %s", u)
                r = await http.get(u)
                if r.status_code in (403, 429):
                    # Throttle the shared limiter so every queued task slows down
                    _rate_limiter.throttle()
                    if attempt >= len(_RETRY_WAITS):
                        if on_page_error is not None:
                            log.error(
                                "Leaderboard %s/%s page %d permanently failed — skipping",
                                region, mode, page,
                            )
                            await on_page_error(page)
                            return []  # skip gracefully
                        r.raise_for_status()
                    wait = _RETRY_WAITS[attempt]
                    log.warning(
                        "Leaderboard %s/%s page %d got %d — pausing %ds (attempt %d/%d)",
                        region, mode, page, r.status_code, wait,
                        attempt + 1, len(_RETRY_WAITS),
                    )
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()["leaderboard"]["rows"]

        end_page = min(total_pages, max_page) if max_page else total_pages
        if end_page > 1:
            for page in range(2, end_page + 1):
                rows = await _fetch_page(page)
                if on_page is not None:
                    await on_page(page, rows)
                _append_rows(entries, rows)

    finally:
        if own_client:
            await http.aclose()

    log.info("Leaderboard %s/%s loaded: %d entries", region, mode, len(entries))
    return entries, season_id


def _append_rows(entries: list[LeaderboardEntry], rows: list[dict]) -> None:
    for row in rows:
        bt = row.get("accountid") or ""
        if not bt:
            continue
        entries.append(LeaderboardEntry(
            rank=int(row["rank"]),
            battletag_orig=bt,
            battletag=bt.lower(),
            rating=row.get("rating"),
        ))
