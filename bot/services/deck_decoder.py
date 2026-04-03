"""
Deck code decoder — wraps the `hearthstone` library and enriches
decoded dbfIds with full CardInfo from HSJsonClient.
"""
import logging
from typing import TYPE_CHECKING

from hearthstone.deckstrings import Deck

from bot.services.models import CardEntry, CardInfo, DeckInfo

if TYPE_CHECKING:
    from bot.services.hs_json_client import HSJsonClient

log = logging.getLogger(__name__)

_FORMAT_LABELS = {
    1: "Wild",
    2: "Standard",
    3: "Classic",
    4: "Twist",
}

_CLASS_BY_HERO_ID: dict[int, str] = {
    # Standard hero dbfIds (Blizzard-defined)
    274:  "Druid",
    671:  "Hunter",
    637:  "Mage",
    1066: "Paladin",
    813:  "Priest",
    930:  "Rogue",
    1214: "Shaman",
    893:  "Warlock",
    7:    "Warrior",
    56550: "Demon Hunter",
    2827: "Death Knight",
}


class DeckDecoder:
    def __init__(self, hs_client: "HSJsonClient") -> None:
        self._client = hs_client

    async def decode(self, code: str) -> DeckInfo:
        """
        Decode a Hearthstone deck code string into a DeckInfo object.

        Accepts either a bare base64 code or the full Hearthstone export block
        (which starts with ### DeckName).  Raises ValueError for malformed codes.
        """
        # Extract deck name and bare code from a full HS export block if present
        deck_name = ""
        bare_code = code.strip()
        for line in code.splitlines():
            line = line.strip()
            if line.startswith("###"):
                deck_name = line.lstrip("#").strip()
            elif line and not line.startswith("#"):
                bare_code = line  # first non-comment, non-empty line is the code
                break

        log.debug("decode start code=%.40s", bare_code)
        try:
            raw_deck = Deck.from_deckstring(bare_code)
        except Exception as exc:
            raise ValueError(f"Could not parse deck code: {exc}") from exc
        log.debug("raw_deck format=%s heroes=%s cards=%s", raw_deck.format, raw_deck.heroes, raw_deck.cards)

        await self._client.ensure_loaded()

        format_id = raw_deck.format
        format_label = _FORMAT_LABELS.get(format_id, f"Format {format_id}")

        # Hero class — try db lookup first, fall back to hardcoded map
        hero_dbf_id = raw_deck.heroes[0] if raw_deck.heroes else 0
        hero_card: CardInfo | None = await self._client.get_card(hero_dbf_id)
        if hero_card:
            hero_class = hero_card.card_class.capitalize()
        else:
            hero_class = _CLASS_BY_HERO_ID.get(hero_dbf_id, "Unknown")

        card_entries: list[CardEntry] = []
        for dbf_id, count in raw_deck.cards:
            card = await self._client.get_card(dbf_id)
            if card is None:
                # Fabled companion dbfIds appear in the deck code alongside the
                # fabled card itself; we add them via get_fabled_companions below,
                # so silently skip them here instead of logging a warning.
                if not await self._client.is_fabled_companion(dbf_id):
                    log.warning("Unknown dbfId=%s in deck, skipping", dbf_id)
                continue
            card_entries.append(CardEntry(card=card, count=count))
            # Fabled cards automatically bring companion cards — add them to the
            # main card list so they appear in every deck view sorted by mana cost.
            companions = await self._client.get_fabled_companions(card.card_id)
            for companion in companions:
                card_entries.append(CardEntry(card=companion, count=1))
            if companions:
                log.debug(
                    "Fabled card %s → %d companions added", card.name, len(companions)
                )

        _ETC_BAND_MANAGER = 90749
        etc_entries: list[CardEntry] = []
        for card_id, count, sideboard_owner in (raw_deck.sideboards or []):
            if sideboard_owner != _ETC_BAND_MANAGER:
                continue
            card = await self._client.get_card(card_id)
            if card is None:
                log.warning("Unknown dbfId=%s in E.T.C. sideboard, skipping", card_id)
                continue
            etc_entries.append(CardEntry(card=card, count=count))

        result = DeckInfo(
            format_id=format_id,
            format_label=format_label,
            hero_dbf_id=hero_dbf_id,
            hero_class=hero_class,
            deck_name=deck_name,
            cards=card_entries,
            etc_sideboard_cards=etc_entries,
        )
        log.debug(
            "decode success class=%s format=%s cards=%d etc_sideboard=%d",
            hero_class, format_label, len(card_entries), len(etc_entries),
        )
        return result
