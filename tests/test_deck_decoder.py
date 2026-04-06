"""
Tests for DeckDecoder — verifies known deck codes decode correctly.
Run with: pytest tests/
"""
import pytest

# A real Standard deck code for quick sanity tests
SAMPLE_WARRIOR_CODE = (
    "AAEBAfWhByi7BfoOwxb8owO2uwP21gPm7gOU/AOpnwTHsgSNtQTnuQTM5ATQ5ASX7wSwkwX9xAWt6QXf7QWX9gXI9gXI+AXT+AWFjgbLjgbUjgbQngaToQaUoQbxpQbi4wbR5QbH9QbO/AavkgeCmAfzmweZpweapwebpwcAAAEDuwX9xAXm7gP9xAWU/AP9xAUAAA=="
)


@pytest.mark.asyncio
async def test_decode_warrior_deck():
    """Smoke test: decode a known Warrior deck code."""
    from unittest.mock import AsyncMock, MagicMock
    from bot.services.deck_decoder import DeckDecoder
    from bot.services.models import CardInfo

    # Mock HSJsonClient
    mock_card = CardInfo(
        dbf_id=1, card_id="CS2_001", name="Warsong Commander",
        cost=3, card_type="MINION", rarity="FREE",
        card_class="WARRIOR", card_set="CORE",
    )
    mock_client = MagicMock()
    mock_client.ensure_loaded = AsyncMock()
    mock_client.get_card = AsyncMock(return_value=mock_card)
    mock_client.get_any_card = AsyncMock(return_value=mock_card)
    mock_client.get_fabled_companions = AsyncMock(return_value=[])
    mock_client.is_fabled_companion = AsyncMock(return_value=False)

    decoder = DeckDecoder(mock_client)
    deck = await decoder.decode(SAMPLE_WARRIOR_CODE)

    assert deck.format_label in ("Standard", "Wild", "Classic", "Twist")
    assert deck.total_cards > 0


@pytest.mark.asyncio
async def test_decode_invalid_code_raises():
    from unittest.mock import AsyncMock, MagicMock
    from bot.services.deck_decoder import DeckDecoder

    mock_client = MagicMock()
    mock_client.ensure_loaded = AsyncMock()
    decoder = DeckDecoder(mock_client)

    with pytest.raises(ValueError):
        await decoder.decode("this-is-not-a-deck-code")


# Deck code containing E.T.C. Band Manager (90749) with 3 sideboard cards:
# sideboard card dbfIds: 1059, 1746, 2039
ETC_DECK_CODE = (
    "AAEBAQcC/cQF1J8GDqcIqAixCMgIoIAD74kDg8MDo9UDnvkDoPkD"
    "6rwEtdAE/sMFrfQFAAEDowj9xAXSDf3EBfcP/cQFAAA="
)
_ETC_BAND_MANAGER_DBF = 90749
_ETC_SIDEBOARD_DBFS = {1059, 1746, 2039}


@pytest.mark.asyncio
async def test_decode_etc_sideboard_cards():
    """E.T.C. Band Manager sideboard cards must appear in etc_sideboard_cards."""
    from unittest.mock import AsyncMock, MagicMock
    from bot.services.deck_decoder import DeckDecoder
    from bot.services.models import CardInfo

    def make_card(dbf_id):
        return CardInfo(
            dbf_id=dbf_id, card_id=f"CARD_{dbf_id}", name=f"Card {dbf_id}",
            cost=1, card_type="MINION", rarity="COMMON",
            card_class="NEUTRAL", card_set="CORE",
        )

    mock_client = MagicMock()
    mock_client.ensure_loaded = AsyncMock()
    mock_client.get_card = AsyncMock(side_effect=lambda dbf_id: make_card(dbf_id))
    mock_client.get_any_card = AsyncMock(side_effect=lambda dbf_id: make_card(dbf_id))
    mock_client.get_fabled_companions = AsyncMock(return_value=[])
    mock_client.is_fabled_companion = AsyncMock(return_value=False)

    decoder = DeckDecoder(mock_client)
    deck = await decoder.decode(ETC_DECK_CODE)

    # E.T.C. must be in the main cards list
    main_dbf_ids = {e.card.dbf_id for e in deck.cards}
    assert _ETC_BAND_MANAGER_DBF in main_dbf_ids

    # Exactly 3 sideboard entries, all owned by E.T.C.
    assert len(deck.etc_sideboard_cards) == 3
    sideboard_dbf_ids = {e.card.dbf_id for e in deck.etc_sideboard_cards}
    assert sideboard_dbf_ids == _ETC_SIDEBOARD_DBFS
