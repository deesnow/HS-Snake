"""
Tests for the <<card name>> auto-detect lookup helpers.
Run with: pytest tests/
"""
import pytest

from bot.commands.auto_detect import extract_card_queries
from bot.commands.search_commands import fuzzy_card_matches, pick_card_matches
from bot.services.models import CardInfo


def _card(name: str) -> CardInfo:
    return CardInfo(
        dbf_id=1, card_id=name.upper(), name=name, cost=4,
        card_type="SPELL", rarity="COMMON", card_class="MAGE", card_set="CORE",
    )


# ── extract_card_queries ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("look at <<Fireball>>", ["Fireball"]),
        ("<<  fire   ball >>", ["fire ball"]),
        ("<<Frost>> and <<Arcane Missiles>>", ["Frost", "Arcane Missiles"]),
        ("<<fire>> <<FIRE>>", ["fire"]),
        ("<<a>> is too short", []),
        ("<<>> empty", []),
        ("no brackets here", []),
        ("<single> brackets", []),
        ("<<one>> <<two>> <<three>> <<four>>", ["one", "two", "three"]),
        ("<<spans\nlines>>", []),
    ],
)
def test_extract_card_queries(text, expected):
    assert extract_card_queries(text) == expected


# ── pick_card_matches ─────────────────────────────────────────────────────

def test_exact_name_match_wins():
    results = [_card("Fireball"), _card("Fireball Volley")]
    assert [c.name for c in pick_card_matches(results, "fireball")] == ["Fireball"]


def test_partial_match_keeps_all():
    results = [_card("Fireball"), _card("Fireball Volley")]
    assert pick_card_matches(results, "fire") == results


def test_no_results():
    assert pick_card_matches([], "anything") == []


# ── fuzzy_card_matches ────────────────────────────────────────────────────

_FUZZY_POOL = [
    _card(n) for n in (
        "Medivh's Triumph", "Gelbin's Triumph", "Medivh the Hallowed",
        "Fireball", "Rolling Fireball", "Eternal Firebolt",
        "Leeroy Jenkins", "Reno Jackson", "Arcane Missiles",
    )
]


@pytest.mark.parametrize(
    "query,expected_first",
    [
        ("medivh's triupmh", "Medivh's Triumph"),
        ("leeroy jenkens", "Leeroy Jenkins"),
        ("reno jakson", "Reno Jackson"),
        ("arcane missles", "Arcane Missiles"),
        ("firebal", "Fireball"),  # whole-name match ranks above Rolling Fireball
    ],
)
def test_fuzzy_finds_misspelled_card(query, expected_first):
    assert fuzzy_card_matches(_FUZZY_POOL, query)[0].name == expected_first


def test_fuzzy_full_name_typo_is_single_result():
    assert [c.name for c in fuzzy_card_matches(_FUZZY_POOL, "medivh's triupmh")] == ["Medivh's Triumph"]


def test_fuzzy_keeps_close_alternatives():
    names = [c.name for c in fuzzy_card_matches(_FUZZY_POOL, "firebal")]
    assert names[:2] == ["Fireball", "Rolling Fireball"]
    assert "Eternal Firebolt" not in names


@pytest.mark.parametrize("query", ["xyzqwv", "", "   "])
def test_fuzzy_rejects_unrelated(query):
    assert fuzzy_card_matches(_FUZZY_POOL, query) == []
