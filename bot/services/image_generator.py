"""
Deck image generator — matches the elise (HearthstoneDeckViewDS) visual style.

Layout
------
• Canvas   = class-specific background image (3000 × ~2344 px, RGBA)
• Card size = dynamically maximised so the grid fills the canvas
              without overlapping the dust-cost strip at y=2150
• Cards     = placed by index (no wrap trigger), edge-to-edge, no gaps
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
CANVAS_W   = 3000   # px — background image width
CANVAS_H   = 2344   # px — background image height

# ── Dust cost text position (fixed, matches background artwork) ────────
DUST_X      = 170
DUST_Y      = 2150
DUST_SIZE   = 140    # Belwe font size at 3000 px canvas width

# ── Bottom branding strip ────────────────────────────────────────────
# The background PNGs bake in 'Deck Viewer' + github link on the right
# side of the bottom strip.  The dust bottle icon lives on the LEFT side
# (x ≈ 0–135) and must be preserved.
# We cover only the right portion where text lives.
BRAND_COVER_X  = 2100   # x from which the text erasure begins
BRAND_COVER_Y  = 2170   # y at which the baked-in text starts
BRAND_CLONE_Y  = 2155   # y of the clean background row used as fill source
BRAND_TITLE    = "Deck Viewer"
BRAND_MARGIN   = 40     # px from right + bottom edges

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

# ── Card aspect ratio ─────────────────────────────────────────────────
# Raw renders are 512×776.  Minion frames (the tightest type) have padding:
#   L=19  R=42  T=65  B=82  → content area 451×629  ratio≈1.395
# We crop by these exact amounts for EVERY card type so all cards receive
# an identical crop — no per-card alpha detection, no size variation.
_CROP_L = 19
_CROP_R = 42
_CROP_T = 65
_CROP_B = 82
# Content dimensions after fixed crop (512-19-42=451, 776-65-82=629)
_CONTENT_W = 512 - _CROP_L - _CROP_R   # 451
_CONTENT_H = 776 - _CROP_T - _CROP_B   # 629
_CARD_RATIO = _CONTENT_H / _CONTENT_W   # ≈ 1.395

# ── Label proportions relative to card_w (derived from elise bucket 500) ─
# original: card_w=500, label=(214,121), offset=(150, 650), card_h≈675 (alpha-cropped)
# The label is pasted BEFORE the card so the card image covers the top of
# the label; the bottom of the label protrudes below the card, sitting in
# the gap between rows — matching the Elise reference layout.
# Our fixed-crop card_h for card_w=500 is 697 px.  Elise label top is at
# 650 px into a 675 px alpha-cropped card → 650/697 of our card_h.
_LBL_W_FRAC  = 214 / 500   # ≈ 0.428
_LBL_H_FRAC  = 121 / 697   # ≈ 0.174  (relative to our fixed-crop card_h)
_LBL_DX_FRAC = 150 / 500   # ≈ 0.300
_LBL_DY_FRAC = 650 / 697   # ≈ 0.933  — label top near card bottom, pasted under card

# ── Row gap ───────────────────────────────────────────────────────────
# The label protrudes ≈ 0.107 × card_h below the card bottom.
# A gap of 0.13 × card_h ensures it is fully visible between rows.
ROW_GAP_FRAC = 0.13

# ── E.T.C. Band Manager dbfId ─────────────────────────────────────────
# Sideboard cards are placed immediately after ETC in the grid and
# rendered with a pale overlay + a gold bracket around the whole group.
ETC_DBF_ID = 90749
ETC_BRACKET_COLOR    = (255, 200,  50, 230)   # gold
ETC_SIDEBOARD_TINT   = (220, 220, 255, 110)   # pale blue-white overlay



def _fixed_crop(im: Image.Image) -> Image.Image:
    """Crop a fixed number of pixels from each side (based on minion frame
    padding measurements).  Every card type receives the same crop so all
    cells appear the same size regardless of frame shape."""
    w, h = im.size
    return im.crop((_CROP_L, _CROP_T, w - _CROP_R, h - _CROP_B))


def _calc_card_size(n: int) -> tuple[int, int, int]:
    """Return (card_w, card_h, cols) that maximises card width so the
    grid fits fully inside the canvas without going below DUST_Y.

    For each candidate column count c (starting from smallest = widest cards):
      card_w = CANVAS_W // c          ← fills the canvas edge-to-edge
      rows   = ceil(n / c)
      total_h = rows * card_h         ← no gap between rows
    Returns the first c whose total_h ≤ DUST_Y.
    """
    for cols in range(1, n + 1):
        card_w = CANVAS_W // cols
        card_h = round(card_w * _CARD_RATIO)
        row_gap = round(card_h * ROW_GAP_FRAC)
        rows   = math.ceil(n / cols)
        if rows * card_h + (rows - 1) * row_gap <= DUST_Y:
            return card_w, card_h, cols
    # Fallback: all cards in one row, minimum width
    card_w = CANVAS_W // n
    return card_w, round(card_w * _CARD_RATIO), n


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
        sorted_main = sorted(
            deck.cards, key=lambda e: (e.card.cost, e.card.card_type, e.card.name)
        )

        sorted_etc_side = (
            sorted(deck.etc_sideboard_cards, key=lambda e: (e.card.cost, e.card.name))
            if deck.etc_sideboard_cards else []
        )

        # ── Build flat entries list ────────────────────────────────────
        # ETC sideboard cards are injected immediately after ETC.
        # Fabled companions are already included in sorted_main (deck.cards).
        entries: list[CardEntry] = []
        etc_group_start: int | None = None
        etc_group_size: int = 0

        for e in sorted_main:
            card_idx = len(entries)

            if e.card.dbf_id == ETC_DBF_ID and etc_group_start is None and sorted_etc_side:
                etc_group_start = card_idx
                etc_group_size = 1 + len(sorted_etc_side)

            entries.append(e)

            if e.card.dbf_id == ETC_DBF_ID and sorted_etc_side and etc_group_start == card_idx:
                entries.extend(sorted_etc_side)

        n = len(entries)

        card_w, card_h, cols = _calc_card_size(n)
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
        row_gap = round(card_h * ROW_GAP_FRAC)
        etc_group_positions: list[tuple[int, int]] = []

        for idx, (entry, raw) in enumerate(zip(entries, image_data)):
            x = (idx % cols) * card_w
            y = (idx // cols) * (card_h + row_gap)

            # ETC group membership
            in_etc_group = (
                etc_group_start is not None
                and etc_group_start <= idx < etc_group_start + etc_group_size
            )
            is_etc_sideboard = in_etc_group and idx > etc_group_start
            if in_etc_group:
                etc_group_positions.append((x, y))

            # ── Count label — pasted BEFORE the card so the card image
            #    covers its top; the bottom protrudes below the card frame.
            if entry.count >= 2:
                label = _load_label(entry.count) if entry.count > 2 else label_default
                if label:
                    canvas.paste(label, (x + lbl_dx, y + lbl_dy), mask=label)

            if raw:
                try:
                    im = Image.open(io.BytesIO(raw)).convert("RGBA")
                    im = _fixed_crop(im)
                    im = im.resize((card_w, card_h), Image.LANCZOS)
                    canvas.paste(im, (x, y), mask=im)
                except Exception:
                    log.debug("Failed to render card %s", entry.card.card_id)

            if is_etc_sideboard:
                tint = Image.new("RGBA", (card_w, card_h), ETC_SIDEBOARD_TINT)
                canvas.paste(tint, (x, y), mask=tint)

        # ── ETC group bracket ─────────────────────────────────────────
        if etc_group_positions:
            bracket_draw = ImageDraw.Draw(canvas)
            pad = max(3, card_w // 120)
            bw  = max(5, card_w // 80)
            rows_map: dict[int, list[int]] = {}
            for (px, py) in etc_group_positions:
                rows_map.setdefault(py, []).append(px)
            for ry, xs in rows_map.items():
                bracket_draw.rectangle(
                    [min(xs) - pad, ry - pad, max(xs) + card_w + pad, ry + card_h + pad],
                    outline=ETC_BRACKET_COLOR,
                    width=bw,
                )

        # ── Dust cost + branding footer ──────────────────────────────────────
        total_dust = sum(_dust_cost(e.card.rarity, e.count) for e in entries)
        draw  = ImageDraw.Draw(canvas)

        # Erase the baked-in 'Deck Viewer' + github text from the background
        # by tiling a clean background row (just above the text) over the area.
        # This preserves the dust bottle icon on the left and leaves no
        # solid-colour patch visible on any class background.
        strip_w = CANVAS_W - BRAND_COVER_X
        clone_row = canvas.crop(
            (BRAND_COVER_X, BRAND_CLONE_Y, CANVAS_W, BRAND_CLONE_Y + 1)
        )  # 1-px tall stripe of clean background
        for ty in range(BRAND_COVER_Y, CANVAS_H):
            canvas.paste(clone_row, (BRAND_COVER_X, ty))
        draw  = ImageDraw.Draw(canvas)   # redraw after paste

        # Dust cost — white text with black stroke; no background rectangle
        font_dust = _load_font(DUST_SIZE)
        draw.text(
            (DUST_X, DUST_Y),
            str(total_dust),
            fill=(255, 255, 255),
            font=font_dust,
            stroke_fill=(0, 0, 0),
            stroke_width=5,
        )

        # 'Deck Viewer' title — same font size and style as dust cost
        font_brand = font_dust
        bbox  = draw.textbbox((0, 0), BRAND_TITLE, font=font_brand)
        bw    = bbox[2] - bbox[0]
        bh    = bbox[3] - bbox[1]
        bx    = CANVAS_W - bw - BRAND_MARGIN
        by    = CANVAS_H - bh - BRAND_MARGIN
        draw.text(
            (bx, by),
            BRAND_TITLE,
            fill=(255, 255, 255),
            font=font_brand,
            stroke_fill=(0, 0, 0),
            stroke_width=5,
        )

        # ── Serialise ─────────────────────────────────────────────────
        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf
