"""
Deck image generator — matches the elise (HearthstoneDeckViewDS) visual style.

Layout
------
• Canvas   = class-specific background image (3000 × ~2344 px, RGBA)
• Card size = dynamically maximised so the grid fills the canvas
              without overlapping the dust-cost strip at y=2150
• Cards     = left-to-right, new row when column exceeds WRAP_AT (2900 px)
• Labels    = PNG overlay images (assets/labels/x2.png … x9.png)
• Dust cost = Belwe text at fixed position (170, 2150)
"""
import asyncio
import io
import logging
import math
import os
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from bot.services.models import CardEntry, DeckInfo

if TYPE_CHECKING:
    from bot.services.hs_json_client import HSJsonClient

log = logging.getLogger(__name__)

# ── Asset paths ────────────────────────────────────────────────────────
BACKS_DIR  = "assets/backs"
LABELS_DIR = "assets/labels"
FONT_PATH  = "assets/fonts/Belwe.ttf"

# ── Background wrap threshold (matches the 3000 px wide background) ────
WRAP_AT    = 2900   # start a new row when col reaches this value
ROW_GAP    = 40     # px between rows

# ── Dust cost text position (fixed, matches background artwork) ────────
DUST_X     = 170
DUST_Y     = 2150
DUST_SIZE  = 140    # Belwe font size at 3000 px canvas width

# ── Class → background file ID ─────────────────────────────────────────
CLASS_BACK_IDS: dict[str, int] = {
    "Warrior":      1,
    "Paladin":      2,
    "Hunter":       3,
    "Rogue":        4,
    "Priest":       5,
    "Shaman":       6,
    "Mage":         7,
    "Warlock":      8,
    "Druid":        9,
    "Demon Hunter": 10,
    "Demonhunter":  10,
    "Death Knight": 14,
    "Deathknight":  14,
}

# ── Card aspect ratio (hearthstonejson 512x renders are 512×776) ───────
_CARD_RATIO = 776 / 512   # ≈ 1.515

# ── Label proportions relative to card_w (derived from elise bucket 500) ─
# original: card_w=500, label=(214,121), offset=(150, 729), card_h=757
_LBL_W_FRAC  = 214 / 500   # ≈ 0.428
_LBL_H_FRAC  = 121 / 757   # ≈ 0.160  (relative to card_h)
_LBL_DX_FRAC = 150 / 500   # ≈ 0.300
_LBL_DY_FRAC = 729 / 757   # ≈ 0.963  (relative to card_h)


def _calc_card_size(n: int) -> tuple[int, int]:
    """​Return (card_w, card_h) that maximises card width so the grid
    fills the canvas height up to but not beyond DUST_Y.

    For a given column count c:
      • cards_per_row = floor(WRAP_AT / card_w) + 1 = c
        ⇒ max card_w = WRAP_AT // (c - 1)  for c > 1,  WRAP_AT for c = 1
      • rows = ceil(n / c)
      • total_h = rows * card_h + (rows - 1) * ROW_GAP

    Iterates c from 1 upward; returns the first (smallest c, largest card_w)
    whose grid fits within the available height.
    """
    max_h = DUST_Y - ROW_GAP  # leave a gap above the dust strip
    for cols in range(1, n + 1):
        card_w = WRAP_AT if cols == 1 else WRAP_AT // (cols - 1)
        card_h = round(card_w * _CARD_RATIO)
        rows   = math.ceil(n / cols)
        total_h = rows * card_h + max(0, rows - 1) * ROW_GAP
        if total_h <= max_h:
            return card_w, card_h
    # Fallback: single row, minimum size
    card_w = WRAP_AT // n
    return card_w, round(card_w * _CARD_RATIO)


def _label_geometry(card_w: int, card_h: int) -> tuple[tuple[int, int], int, int]:
    """Return (label_wh, offset_x, offset_y) scaled to the given card size."""
    lbl_w  = max(1, round(card_w * _LBL_W_FRAC))
    lbl_h  = max(1, round(card_h * _LBL_H_FRAC))
    lbl_dx = round(card_w * _LBL_DX_FRAC)
    lbl_dy = round(card_h * _LBL_DY_FRAC)
    return (lbl_w, lbl_h), lbl_dx, lbl_dy


def _dust_cost(rarity: str, count: int) -> int:
    costs = {"FREE": 0, "COMMON": 40, "RARE": 100, "EPIC": 400, "LEGENDARY": 1600}
    per_copy = costs.get(rarity.upper(), 0)
    return per_copy if rarity.upper() == "LEGENDARY" else per_copy * count


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if os.path.exists(FONT_PATH):
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default(size=size)


class ImageGenerator:
    def __init__(self, hs_client: "HSJsonClient") -> None:
        self._client = hs_client

    async def generate_deck_image(self, deck: DeckInfo) -> io.BytesIO:
        entries = sorted(
            deck.cards, key=lambda e: (e.card.cost, e.card.card_type, e.card.name)
        )
        n = len(entries)

        card_w, card_h = _calc_card_size(n)
        label_wh, lbl_dx, lbl_dy = _label_geometry(card_w, card_h)

        # ── Load background ───────────────────────────────────────────
        back_id   = CLASS_BACK_IDS.get(deck.hero_class, 0)
        back_path = os.path.join(BACKS_DIR, f"{back_id}.png")
        try:
            canvas = Image.open(back_path).convert("RGBA")
        except Exception:
            log.warning("Background not found for class %s, using solid colour", deck.hero_class)
            canvas = Image.new("RGBA", (3000, 2344), (15, 15, 30, 255))

        # ── Pre-load x2 label (default); others loaded on demand ──────
        def _load_label(count: int) -> Image.Image | None:
            path = os.path.join(LABELS_DIR, f"x{min(count, 9)}.png")
            try:
                return Image.open(path).convert("RGBA").resize(label_wh, Image.LANCZOS)
            except Exception:
                return None

        label_default = _load_label(2)  # x2 — used for all 2-copy cards

        # ── Fetch all card images concurrently ────────────────────────
        async def _fetch(entry: CardEntry) -> bytes | None:
            try:
                card_id = entry.card.card_id
                url = (
                    f"https://art.hearthstonejson.com/v1/render/latest"
                    f"/enUS/512x/{card_id}.png"
                )
                client = await self._client._client()
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
            except Exception:
                log.debug("Image unavailable for %s", entry.card.card_id)
                return None

        image_data: list[bytes | None] = list(
            await asyncio.gather(*[_fetch(e) for e in entries])
        )

        # ── Place cards ───────────────────────────────────────────────
        col, row = 0, 0

        for entry, raw in zip(entries, image_data):
            if raw:
                try:
                    im = Image.open(io.BytesIO(raw)).convert("RGBA")
                    # All hearthstonejson renders are 512×776 — resize uniformly
                    # (no alpha-crop) so every card occupies exactly card_w×card_h.
                    # Paste with alpha mask so the card frame transparency
                    # shows the class background art instead of black.
                    im = im.resize((card_w, card_h), Image.LANCZOS)
                    canvas.paste(im, (col, row), mask=im)
                except Exception:
                    log.debug("Failed to render card %s", entry.card.card_id)

            # ── Count label overlay ───────────────────────────────────
            if entry.count >= 2:
                label = _load_label(entry.count) if entry.count > 2 else label_default
                if label:
                    canvas.paste(label, (col + lbl_dx, row + lbl_dy), mask=label)

            col += card_w
            if col > WRAP_AT:
                col = 0
                row += card_h + ROW_GAP

        # ── Dust cost ─────────────────────────────────────────────────
        total_dust = sum(_dust_cost(e.card.rarity, e.count) for e in entries)
        font  = _load_font(DUST_SIZE)
        draw  = ImageDraw.Draw(canvas)
        draw.text(
            (DUST_X, DUST_Y),
            str(total_dust),
            fill=(255, 255, 255),
            font=font,
            stroke_fill=(0, 0, 0),
            stroke_width=5,
        )

        # ── Serialise ─────────────────────────────────────────────────
        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf
