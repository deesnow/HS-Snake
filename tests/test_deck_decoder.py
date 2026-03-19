"""
Tests for DeckDecoder — verifies known deck codes decode correctly.
Run with: pytest tests/
"""
import pytest

# A real Standard deck code for quick sanity tests
SAMPLE_WARRIOR_CODE = (
    "AAECAQcGkrEE2scE09AEgtEEh9IEvNIEDPoBqAKUA5wDpAOWBZMH"
    "kAiko4IE6KMIV6WEBS/gBwA="
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
