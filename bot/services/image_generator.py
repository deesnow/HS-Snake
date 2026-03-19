"""
Deck image generator — card art grid layout.

Cards are arranged in a 5-column grid.  The count label (1x / 2x) is
drawn directly onto the black border at the bottom of the card image.
All card images are fetched concurrently.

Canvas width  = 5 * CARD_W + 6 * PAD
Canvas height = HEADER_H + rows * CARD_H + (rows + 1) * PAD
"""
import asyncio
import io
import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from bot.services.models import CardEntry, DeckInfo

if TYPE_CHECKING:
    from bot.services.hs_json_client import HSJsonClient

log = logging.getLogger(__name__)

# ── Layout constants ───────────────────────────────────────────────────
MAX_COLS    = 7            # never more than this many cards per row
CARD_W      = 220          # px — each card cell width
CARD_H      = 308          # px — card image height (~1:1.4 aspect)
LABEL_INSET = 8            # px — gap from card bottom edge to label baseline
PAD         = 10           # px — gap between cells and edges
HEADER_H    = 60           # px — top header bar


def _calc_grid(n: int) -> tuple[int, int]:
    """Return (cols, rows) that maximises cols while keeping cols ≤ MAX_COLS
    and distributes cards as evenly as possible across rows."""
    rows = max(1, -(-n // MAX_COLS))          # ceil(n / MAX_COLS)
    cols = -(-n // rows)                      # ceil(n / rows)
    return cols, rows

BG_COLOUR   = (15,  15,  30)
HEADER_BG   = (25,  25,  50)
LABEL_FG    = (255, 200, 50)   # gold
PLACEHOLDER = (40,  40,  70)   # fill used when image unavailable

_FONT_PATH  = "assets/fonts/BelweGothic.ttf"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_PATH
    if bold:
        bold_path = _FONT_PATH.replace(".ttf", "-Bold.ttf")
        import os
        if os.path.exists(bold_path):
            path = bold_path
    try:
        return ImageFont.truetype(path, size)
    except (IOError, OSError):
        return ImageFont.load_default()


class ImageGenerator:
    def __init__(self, hs_client: "HSJsonClient") -> None:
        self._client = hs_client

    async def generate_deck_image(self, deck: DeckInfo) -> io.BytesIO:
        entries = sorted(
            deck.cards, key=lambda e: (e.card.cost, e.card.card_type, e.card.name)
        )
        n            = len(entries)
        cols, rows   = _calc_grid(n)

        total_w = cols * CARD_W + (cols + 1) * PAD
        total_h = HEADER_H + rows * CARD_H + (rows + 1) * PAD

        canvas = Image.new("RGB", (total_w, total_h), BG_COLOUR)
        draw   = ImageDraw.Draw(canvas)

        font_hdr   = _load_font(20)
        font_sm    = _load_font(13)
        font_badge = _load_font(18, bold=True)
        # ── Header ────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (total_w, HEADER_H)], fill=HEADER_BG)
        draw.text((PAD * 2, 10), deck.hero_class,   font=font_hdr, fill=(220, 220, 220))
        draw.text(
            (PAD * 2, 34),
            f"{deck.format_label}  ·  {deck.total_cards} cards",
            font=font_sm,
            fill=(160, 160, 200),
        )

        # ── Fetch all card images concurrently ────────────────────────
        async def _fetch(entry: CardEntry) -> bytes | None:
            try:
                return await self._client.get_card_image_bytes(
                    entry.card.card_id, entry.card.dbf_id
                )
            except Exception:
                log.debug("Image unavailable for %s", entry.card.card_id)
                return None

        image_data: list[bytes | None] = list(
            await asyncio.gather(*[_fetch(e) for e in entries])
        )

        # ── Grid ──────────────────────────────────────────────────────
        for idx, (entry, raw) in enumerate(zip(entries, image_data)):
            col = idx % cols
            row = idx // cols

            x = PAD + col * (CARD_W + PAD)
            y = HEADER_H + PAD + row * (CARD_H + PAD)

            # Card image
            if raw:
                try:
                    img = Image.open(io.BytesIO(raw)).convert("RGB")
                    img = img.resize((CARD_W, CARD_H), Image.LANCZOS)
                    canvas.paste(img, (x, y))
                except Exception:
                    draw.rectangle([(x, y), (x + CARD_W, y + CARD_H)], fill=PLACEHOLDER)
            else:
                draw.rectangle([(x, y), (x + CARD_W, y + CARD_H)], fill=PLACEHOLDER)

            # Count label — drawn inside the card's black bottom border
            label = f"{entry.count}x"
            bbox  = draw.textbbox((0, 0), label, font=font_badge)
            lw    = bbox[2] - bbox[0]
            lh    = bbox[3] - bbox[1]
            draw.text(
                (x + (CARD_W - lw) // 2, y + CARD_H - lh - LABEL_INSET),
                label,
                font=font_badge,
                fill=LABEL_FG,
            )

        # ── Serialise ─────────────────────────────────────────────────
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf

