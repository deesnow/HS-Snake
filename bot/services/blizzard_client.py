"""
Blizzard Battle.net Hearthstone API client — fallback card lookup for
dbfIds HearthstoneJSON hasn't indexed yet (e.g. cards revealed hours or
days before the community HearthstoneJSON dump catches up).

Fully opt-in: when BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET are unset,
every method returns None immediately and no network calls are made.

Token and metadata are cached at module scope (mirroring hs_json_client's
_card_db singleton pattern) so every DeckDecoder instance shares one
access token and one metadata lookup instead of re-authenticating per cog.
"""
import asyncio
import logging
import time
from typing import Optional

import httpx

from bot.config import settings
from bot.services.models import CardInfo

log = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth.battle.net/token"
_API_BASE = "https://us.api.blizzard.com/hearthstone"

# Refresh the token a bit before it actually expires to avoid racing a
# request against expiry.
_TOKEN_REFRESH_MARGIN_SECONDS = 60

_token_lock = asyncio.Lock()
_access_token: Optional[str] = None
_token_expires_at: float = 0.0

_metadata_lock = asyncio.Lock()
_metadata: Optional[dict[str, dict[int, str]]] = None


class BlizzardClient:
    """Fetches individual cards from Blizzard's live Hearthstone Game Data API."""

    def __init__(self) -> None:
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return bool(settings.blizzard_client_id and settings.blizzard_client_secret)

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def _ensure_token(self) -> Optional[str]:
        global _access_token, _token_expires_at
        if not self.enabled:
            return None

        async with _token_lock:
            if _access_token and time.time() < _token_expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
                return _access_token

            client = await self._client()
            try:
                resp = await client.post(
                    _TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    auth=(settings.blizzard_client_id, settings.blizzard_client_secret),
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                log.warning("Blizzard API token request failed", exc_info=True)
                return None

            _access_token = data["access_token"]
            _token_expires_at = time.time() + data.get("expires_in", 0)
            log.info("Blizzard API access token acquired (expires in %ss)", data.get("expires_in"))
            return _access_token

    async def _ensure_metadata(self, token: str) -> dict[str, dict[int, str]]:
        global _metadata
        if _metadata is not None:
            return _metadata

        async with _metadata_lock:
            if _metadata is not None:
                return _metadata

            client = await self._client()
            resp = await client.get(
                f"{_API_BASE}/metadata",
                params={"locale": "en_US"},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            def _id_map(key: str) -> dict[int, str]:
                return {entry["id"]: entry["name"] for entry in data.get(key, []) if "id" in entry}

            _metadata = {
                "classes": _id_map("classes"),
                "types": _id_map("types"),
                "rarities": _id_map("rarities"),
                "minionTypes": _id_map("minionTypes"),
                "spellSchools": _id_map("spellSchools"),
                "sets": _id_map("sets"),
            }
            return _metadata

    async def get_card_by_dbf_id(self, dbf_id: int) -> Optional[CardInfo]:
        """Fetch a single card by dbfId directly from Blizzard's API.

        Blizzard's numeric card `id` is the same dbfId used in deck codes,
        so this can be called with the exact id that failed to resolve
        against the local HearthstoneJSON database.

        Returns None if the feature is disabled, auth/metadata fails, or
        the card genuinely doesn't exist there either.
        """
        token = await self._ensure_token()
        if token is None:
            return None

        client = await self._client()
        try:
            resp = await client.get(
                f"{_API_BASE}/cards/{dbf_id}",
                params={"locale": "en_US"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            raw = resp.json()
            meta = await self._ensure_metadata(token)
        except Exception:
            log.warning("Blizzard API lookup failed for dbfId=%s", dbf_id, exc_info=True)
            return None

        # HearthstoneJSON's short string card id (e.g. "LOE_011") has no
        # Blizzard API equivalent — synthesize a stable placeholder. It's
        # only used as a Discord attachment filename and as a dict key for
        # fabled-companion lookups (which will simply miss, same as any
        # other card without registered companions).
        card_id = f"BLIZZARD_{dbf_id}"

        return CardInfo(
            dbf_id=dbf_id,
            card_id=card_id,
            name=raw.get("name", "Unknown"),
            cost=raw.get("manaCost", 0),
            card_type=meta["types"].get(raw.get("cardTypeId"), "UNKNOWN").upper(),
            rarity=meta["rarities"].get(raw.get("rarityId"), "FREE").upper(),
            # HearthstoneJSON's cardClass has no spaces ("DEATHKNIGHT"); match
            # that convention so class-based lookups elsewhere keep working.
            card_class=meta["classes"].get(raw.get("classId"), "Neutral").upper().replace(" ", ""),
            card_set=meta["sets"].get(raw.get("cardSetId"), ""),
            text=raw.get("text"),
            attack=raw.get("attack"),
            health=raw.get("health"),
            durability=raw.get("durability"),
            flavor=raw.get("flavorText"),
            race=meta["minionTypes"].get(raw.get("minionTypeId")) if raw.get("minionTypeId") else None,
            spell_school=meta["spellSchools"].get(raw.get("spellSchoolId")) if raw.get("spellSchoolId") else None,
            image_url=raw.get("image"),
        )
