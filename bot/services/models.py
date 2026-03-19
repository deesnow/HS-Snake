"""
Shared data models for card and deck information.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CardInfo:
    dbf_id: int
    card_id: str
    name: str
    cost: int
    card_type: str          # MINION, SPELL, WEAPON, HERO, LOCATION
    rarity: str             # FREE, COMMON, RARE, EPIC, LEGENDARY
    card_class: str         # MAGE, WARRIOR, NEUTRAL, …
    card_set: str
    text: Optional[str] = None
    attack: Optional[int] = None
    health: Optional[int] = None
    durability: Optional[int] = None
    flavor: Optional[str] = None


@dataclass
class CardEntry:
    card: CardInfo
    count: int              # 1 or 2 (or more for special cards)


@dataclass
class DeckInfo:
    format_id: int
    format_label: str
    hero_dbf_id: int
    hero_class: str
    cards: list = field(default_factory=list)   # List[CardEntry]

    @property
    def total_cards(self) -> int:
        return sum(e.count for e in self.cards)
