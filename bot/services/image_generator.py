"""
Deck image generator.

Composes a deck's card renders into a single PNG image using Pillow.

Layout (width = 800 px):
  ┌──────────────────────────────────────────┐
  │  HEADER: class name + format badge        │
  ├──────────────────────────────────────────┤
  │  [card tile] [name]            [cost] x2  │  ← TILE_H px per card
  │  ...                                      │
  ├──────────────────────────────────────────┤
  │  FOOTER: card count + dust cost           │
  └──────────────────────────────────────────┘
"""
import io
import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from bot.services.models import CardEntry, DeckInfo

if TYPE_CHECKING:
    from bot.services.hs_json_client import HSJsonClient

log = logging.getLogger(__name__)

# ── Layout constants ──────────────────────────────────────────────────
IMG_WIDTH   = 800
HEADER_H    = 80
FOOTER_H    = 50
TILE_H      = 64          # height of one card row
TILE_IMG_W  = 200         # width reserved for the card thumbnail
RARITY_BAR  = 6           # left-edge rarity colour stripe width
BG_COLOUR   = (15, 15, 30)        # dark navy background
HEADER_BG   = (25, 25, 50)
FOOTER_BG   = (25, 25, 50)
TEXT_COLOUR = (220, 220, 220)
ROW_ALT     = (22, 22, 44)        # alternating row tint

RARITY_COLOURS: dict[str, tuple[int, int, int]] = {
    "FREE":       (180, 180, 180),
    "COMMON":     (255, 255, 255),
    "RARE":       (0,   112, 221),
    "EPIC":       (163, 53,  238),
    "LEGENDARY":  (255, 128, 0),
}

# Font — use a bundled TTF if available, otherwise fall back to default
_FONT_PATH = "assets/fonts/BelweGothic.ttf"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_FONT_PATH, size)
    except (IOError, OSError):
        return ImageFont.load_default()


def _dust_total(cards: list[CardEntry]) -> int:
    costs = {"FREE": 0, "COMMON": 40, "RARE": 100, "EPIC": 400, "LEGENDARY": 1600}
    total = 0
    for entry in cards:
        per = costs.get(entry.card.rarity.upper(), 0)
        total += per if entry.card.rarity.upper() == "LEGENDARY" else per * entry.count
    return total


class ImageGenerator:
    def __init__(self, hs_client: "HSJsonClient") -> None:
        self._client = hs_client

    async def generate_deck_image(self, deck: DeckInfo) -> io.BytesIO:
        sorted_cards = sorted(
            deck.cards, key=lambda e: (e.card.cost, e.card.card_type, e.card.name)
        )
        n_cards = len(sorted_cards)
        total_h = HEADER_H + n_cards * TILE_H + FOOTER_H

        canvas = Image.new("RGB", (IMG_WIDTH, total_h), BG_COLOUR)
        draw = ImageDraw.Draw(canvas)

        font_lg  = _load_font(22)
        font_md  = _load_font(16)
        font_sm  = _load_font(13)

        # ── Header ───────────────────────────────────────────────────
        draw.rectangle([(0, 0), (IMG_WIDTH, HEADER_H)], fill=HEADER_BG)
        draw.text((20, 14), deck.hero_class, font=font_lg, fill=TEXT_COLOUR)
        draw.text((20, 44), f"Format: {deck.format_label}", font=font_sm, fill=(160, 160, 190))
        format_badge = f" {deck.format_label} "
        bbox = draw.textbbox((0, 0), format_badge, font=font_sm)
        badge_w = bbox[2] - bbox[0] + 12
        badge_x = IMG_WIDTH - badge_w - 16
        draw.rounded_rectangle(
            [(badge_x, 24), (badge_x + badge_w, 24 + 24)],
            radius=6,
            fill=(50, 80, 140),
        )
        draw.text((badge_x + 6, 26), format_badge.strip(), font=font_sm, fill=(200, 220, 255))

        # ── Card rows ────────────────────────────────────────────────
        for idx, entry in enumerate(sorted_cards):
            y = HEADER_H + idx * TILE_H
            # Alternating row background
            row_bg = ROW_ALT if idx % 2 == 0 else BG_COLOUR
            draw.rectangle([(0, y), (IMG_WIDTH, y + TILE_H)], fill=row_bg)

            # Rarity bar
            rarity_col = RARITY_COLOURS.get(entry.card.rarity.upper(), (180, 180, 180))
            draw.rectangle([(0, y), (RARITY_BAR, y + TILE_H)], fill=rarity_col)

            # Card thumbnail image
            try:
                img_bytes = await self._client.get_card_image_bytes(entry.card.dbf_id)
                card_img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                # Crop to tile dimensions: use the left portion (character art)
                tile_aspect_w = int(card_img.width * TILE_H / card_img.height)
                card_img = card_img.resize((tile_aspect_w, TILE_H), Image.LANCZOS)
                # Crop to TILE_IMG_W
                card_img = card_img.crop((0, 0, min(tile_aspect_w, TILE_IMG_W), TILE_H))
                # Paste with alpha mask
                if card_img.mode == "RGBA":
                    canvas.paste(card_img, (RARITY_BAR, y), card_img)
                else:
                    canvas.paste(card_img, (RARITY_BAR, y))
            except Exception:
                log.debug("Failed to fetch image for dbfId=%s", entry.card.dbf_id)

            # Card name
            text_x = RARITY_BAR + TILE_IMG_W + 10
            name = entry.card.name
            # Truncate long names
            while draw.textlength(name, font=font_md) > 380 and len(name) > 4:
                name = name[:-2]
            if name != entry.card.name:
                name += "…"
            draw.text((text_x, y + 14), name, font=font_md, fill=TEXT_COLOUR)
            # Card type hint
            draw.text(
                (text_x, y + 36),
                entry.card.card_type.capitalize(),
                font=font_sm,
                fill=(120, 120, 150),
            )

            # Count badge (×2)
            if entry.count > 1:
                count_label = f"×{entry.count}"
                cx = IMG_WIDTH - 80
                draw.text((cx, y + 22), count_label, font=font_md, fill=rarity_col)

            # Mana cost bubble
            mana_label = str(entry.card.cost)
            mana_x = IMG_WIDTH - 48
            draw.ellipse([(mana_x, y + 10), (mana_x + 34, y + 10 + 34)], fill=(0, 60, 180))
            mb = draw.textbbox((0, 0), mana_label, font=font_md)
            mw = mb[2] - mb[0]
            draw.text(
                (mana_x + (34 - mw) // 2, y + 14),
                mana_label,
                font=font_md,
                fill=(255, 255, 255),
            )

        # ── Footer ───────────────────────────────────────────────────
        fy = HEADER_H + n_cards * TILE_H
        draw.rectangle([(0, fy), (IMG_WIDTH, fy + FOOTER_H)], fill=FOOTER_BG)
        dust = _dust_total(deck.cards)
        footer_text = (
            f"Total cards: {deck.total_cards}   |   "
            f"Crafting cost: {dust:,} dust"
        )
        draw.text((20, fy + 14), footer_text, font=font_sm, fill=(160, 160, 190))

        # ── Serialise to BytesIO ──────────────────────────────────────
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf
