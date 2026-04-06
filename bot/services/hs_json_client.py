"""
HearthstoneJSON API client with local Nginx cache fall-through.

On startup, fetches all-cards JSON once and builds an in-memory lookup.
Card images are fetched through the local Nginx cache container.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from bot.config import settings
from bot.services.models import CardInfo

log = logging.getLogger(__name__)

_HSJSON_CARDS_URL = (
    "https://api.hearthstonejson.com/v1/latest/{locale}/cards.json"
)
_HSJSON_ART_URL = (
    "https://art.hearthstonejson.com/v1/render/latest/{locale}/{size}/{card_id}.png"
)

# Singleton lock so multiple cogs share one loaded DB
_db_lock = asyncio.Lock()
_card_db: dict[int, CardInfo] = {}                      # dbfId → CardInfo (collectible only)
_all_cards_db: dict[int, CardInfo] = {}                 # dbfId → CardInfo (collectible + non-collectible)
_name_index: dict[str, CardInfo] = {}                   # lower-name → CardInfo
_fabled_companions_by_card_id: dict[str, list[CardInfo]] = {}  # fabled card_id → companions
_companion_dbf_ids: set[int] = set()                    # all companion dbfIds (for skip logic)

# Companions are non-collectible tokens whose card ID matches {fabled_base}t followed
# by only digits (e.g. TIME_852t1, TIME_852t3).  Deeper sub-tokens like TIME_020t2t or
# TIME_005t2e are excluded by this pattern.
_COMPANION_SUFFIX_RE = re.compile(r"^t\d*$")

_CACHE_TTL_SECONDS = 86_400                 # 24 hours
_CACHE_FILE = Path(os.getenv("DATA_DIR", "./data")) / "cards_cache.json"


def _build_card_info(raw: dict) -> Optional[CardInfo]:
    """Convert a raw JSON card object into a CardInfo, or None if unusable."""
    dbf_id = raw.get("dbfId")
    card_id = raw.get("id")
    name = raw.get("name")
    if not (dbf_id and card_id and name):
        return None
    return CardInfo(
        dbf_id=int(dbf_id),
        card_id=card_id,
        name=name,
        cost=raw.get("cost", 0),
        card_type=raw.get("type", "UNKNOWN"),
        rarity=raw.get("rarity", "FREE"),
        card_class=raw.get("cardClass", "NEUTRAL"),
        card_set=raw.get("set", ""),
        text=raw.get("text"),
        attack=raw.get("attack"),
        health=raw.get("health"),
        durability=raw.get("durability"),
        flavor=raw.get("flavor"),
        race=raw.get("race"),
        spell_school=raw.get("spellSchool"),
    )


def _parse_card(raw: dict) -> Optional[CardInfo]:
    """Return a CardInfo only for collectible cards."""
    if not raw.get("collectible"):
        return None
    return _build_card_info(raw)


def _is_fabled_raw(raw: dict) -> bool:
    """True if the raw card JSON represents a fabled card.

    In hearthstonejson the fabled keyword lives in the card text as
    '<b>Fabled</b>', not in the mechanics array.
    """
    text: str = raw.get("text") or ""
    return bool(re.search(r"\bFabled\b", text, re.IGNORECASE))


class HSJsonClient:
    """Fetches and caches Hearthstone card data from HearthstoneJSON."""

    def __init__(self) -> None:
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._http

    # ------------------------------------------------------------------
    # Card database
    # ------------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Load the card DB once per process; uses a disk cache valid for 24 h."""
        global _card_db, _all_cards_db, _name_index, _fabled_companions_by_card_id, _companion_dbf_ids
        async with _db_lock:
            if _card_db:
                return

            raw_cards: Optional[list[dict]] = None

            # Try disk cache first
            if _CACHE_FILE.exists():
                age = time.time() - _CACHE_FILE.stat().st_mtime
                if age < _CACHE_TTL_SECONDS:
                    log.info("Loading card database from disk cache (age %.0f s)…", age)
                    raw_cards = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                else:
                    log.info("Disk cache expired (age %.0f s), re-fetching…", age)

            if raw_cards is None:
                log.info("Fetching card database from HearthstoneJSON…")
                url = _HSJSON_CARDS_URL.format(locale=settings.hsjson_locale)
                client = await self._client()
                response = await client.get(url)
                response.raise_for_status()
                raw_cards = response.json()
                # Persist to disk
                _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                _CACHE_FILE.write_text(json.dumps(raw_cards), encoding="utf-8")
                log.info("Card database saved to disk cache: %s", _CACHE_FILE)

            for raw in raw_cards:
                any_card = _build_card_info(raw)
                if any_card:
                    _all_cards_db[any_card.dbf_id] = any_card
                if raw.get("collectible") and any_card:
                    _card_db[any_card.dbf_id] = any_card
                    _name_index[any_card.name.lower()] = any_card
            log.info("Card database loaded: %d collectible, %d total cards", len(_card_db), len(_all_cards_db))

            # Build fabled companion lookup.
            # hearthstonejson has no childIds field; companions are identified by
            # card ID suffix: a fabled card TIME_852 has companions TIME_852t1,
            # TIME_852t3, etc. (the suffix after the base ID matches /^t\d*$/).
            # Sub-tokens like TIME_020t2t or TIME_005t2e are excluded automatically.
            fabled_card_ids: set[str] = {
                raw["id"]
                for raw in raw_cards
                if raw.get("id") and raw.get("collectible") and _is_fabled_raw(raw)
            }
            log.debug("Fabled collectible cards found: %d", len(fabled_card_ids))

            for raw in raw_cards:
                if raw.get("collectible") or not raw.get("id"):
                    continue
                card_id: str = raw["id"]
                for fabled_id in fabled_card_ids:
                    if not card_id.startswith(fabled_id):
                        continue
                    suffix = card_id[len(fabled_id):]
                    if _COMPANION_SUFFIX_RE.match(suffix):
                        companion = _build_card_info(raw)
                        if companion:
                            _fabled_companions_by_card_id.setdefault(fabled_id, []).append(companion)
                            _companion_dbf_ids.add(companion.dbf_id)
                        break

            total_companions = sum(len(v) for v in _fabled_companions_by_card_id.values())
            log.info(
                "Fabled companion DB built: %d companions across %d fabled cards",
                total_companions, len(_fabled_companions_by_card_id),
            )

    async def get_card(self, dbf_id: int) -> Optional[CardInfo]:
        await self.ensure_loaded()
        return _card_db.get(dbf_id)

    async def get_any_card(self, dbf_id: int) -> Optional[CardInfo]:
        """Like get_card, but also finds non-collectible cards (e.g. Zilliax modules)."""
        await self.ensure_loaded()
        return _all_cards_db.get(dbf_id)

    async def get_fabled_companions(self, card_id: str) -> list[CardInfo]:
        """Return the fabled companion CardInfos for a given collectible card_id.

        Returns an empty list if the card is not fabled or has no companions.
        """
        await self.ensure_loaded()
        return list(_fabled_companions_by_card_id.get(card_id, []))

    async def is_fabled_companion(self, dbf_id: int) -> bool:
        """True if this dbfId is a non-collectible fabled companion card."""
        await self.ensure_loaded()
        return dbf_id in _companion_dbf_ids

    async def find_card_by_name(self, name: str) -> Optional[CardInfo]:
        """Case-insensitive exact match first, then partial match."""
        await self.ensure_loaded()
        key = name.lower()
        if key in _name_index:
            return _name_index[key]
        # Partial match fallback
        matches = [c for k, c in _name_index.items() if key in k]
        return matches[0] if matches else None

    async def search_cards(
        self,
        *,
        cost: Optional[int] = None,       # None = any; -1 = 10+
        card_class: Optional[str] = None, # None = any; "NEUTRAL" etc.
        card_type: Optional[str] = None,  # None = any; "MINION" etc.
        name_query: str = "",
        limit: int = 100,
    ) -> list[CardInfo]:
        """Return cards matching all supplied filters, sorted by cost then name.

        Deduplicates by name — when the same card exists in multiple sets
        (e.g. CORE reprints), the canonical non-reprint version is kept.
        """
        _REPRINT_SETS = {"CORE", "VANILLA", "CREDITS", "HERO_SKINS", "INVALID"}

        await self.ensure_loaded()
        # name (lower) → best candidate so far
        seen: dict[str, CardInfo] = {}
        nq = name_query.lower()
        for card in _card_db.values():
            if card_type and card.card_type.upper() != card_type.upper():
                continue
            if card_class and card.card_class.upper() != card_class.upper():
                continue
            if cost is not None:
                if cost == -1:   # 10+
                    if card.cost < 10:
                        continue
                else:
                    if card.cost != cost:
                        continue
            if nq and nq not in card.name.lower():
                continue

            key = card.name.lower()
            existing = seen.get(key)
            if existing is None:
                seen[key] = card
            else:
                # Prefer canonical set over reprint sets
                existing_is_reprint = existing.card_set.upper() in _REPRINT_SETS
                card_is_reprint = card.card_set.upper() in _REPRINT_SETS
                if existing_is_reprint and not card_is_reprint:
                    seen[key] = card

        results = list(seen.values())
        results.sort(key=lambda c: (c.cost, c.name))
        return results[:limit]

    # ------------------------------------------------------------------
    # Card images
    # ------------------------------------------------------------------

    async def get_card_image_bytes(self, card_id: str, dbf_id: int) -> bytes:
        """
        Fetch card render PNG bytes.

        Tries the local Nginx cache first (CACHE_BASE_URL/cards/{card_id}.png).
        Falls back to the upstream HearthstoneJSON art endpoint.
        """
        cache_url = (
            f"{settings.cache_base_url}/cards/"
            f"{settings.hsjson_locale}/{settings.image_card_size}/{card_id}.png"
        )
        upstream_url = _HSJSON_ART_URL.format(
            locale=settings.hsjson_locale,
            size=settings.image_card_size,
            card_id=card_id,
        )
        client = await self._client()
        # Try cache
        try:
            log.debug("GET %s", cache_url)
            resp = await client.get(cache_url)
            log.debug("← %s %s", resp.status_code, cache_url)
            if resp.status_code == 200:
                log.debug("cache hit card_id=%s", card_id)
                return resp.content
        except Exception:
            log.debug("cache unavailable for card_id=%s, falling back to upstream", card_id)
        # Fall back to upstream
        log.debug("GET %s", upstream_url)
        resp = await client.get(upstream_url)
        log.debug("← %s %s", resp.status_code, upstream_url)
        resp.raise_for_status()
        return resp.content
