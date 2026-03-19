"""
HearthstoneJSON API client with local Nginx cache fall-through.

On startup, fetches all-cards JSON once and builds an in-memory lookup.
Card images are fetched through the local Nginx cache container.
"""
import asyncio
import logging
import os
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
_card_db: dict[int, CardInfo] = {}          # dbfId → CardInfo
_name_index: dict[str, CardInfo] = {}       # lower-name → CardInfo


def _parse_card(raw: dict) -> Optional[CardInfo]:
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
    )


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
        """Load the card DB once; subsequent calls are no-ops."""
        global _card_db, _name_index
        async with _db_lock:
            if _card_db:
                return
            log.info("Fetching card database from HearthstoneJSON…")
            url = _HSJSON_CARDS_URL.format(locale=settings.hsjson_locale)
            client = await self._client()
            response = await client.get(url)
            response.raise_for_status()
            raw_cards: list[dict] = response.json()
            for raw in raw_cards:
                card = _parse_card(raw)
                if card:
                    _card_db[card.dbf_id] = card
                    _name_index[card.name.lower()] = card
            log.info("Card database loaded: %d cards", len(_card_db))

    async def get_card(self, dbf_id: int) -> Optional[CardInfo]:
        await self.ensure_loaded()
        return _card_db.get(dbf_id)

    async def find_card_by_name(self, name: str) -> Optional[CardInfo]:
        """Case-insensitive exact match first, then partial match."""
        await self.ensure_loaded()
        key = name.lower()
        if key in _name_index:
            return _name_index[key]
        # Partial match fallback
        matches = [c for k, c in _name_index.items() if key in k]
        return matches[0] if matches else None

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
            resp = await client.get(cache_url)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            log.debug("Cache miss or unavailable for dbfId=%s, falling back", dbf_id)
        # Fall back to upstream
        resp = await client.get(upstream_url)
        resp.raise_for_status()
        return resp.content
