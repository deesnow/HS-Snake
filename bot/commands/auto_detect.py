"""
Automatic deck code and card name detection — listens to messages and
responds when a valid Hearthstone deck string or a <<card name>> is found.

Deck detection pipeline
-----------------------
1. Regex scan  — find base64-like token(s) in message text
2. Base64 test — must decode cleanly to bytes
3. Deck parse  — hearthstone library must parse it as a valid deck
4. Reply       — per the guild's /botadmin decktype: card list (as /deck, the
                 default) or deck image (as /deckimage)

Card lookup
-----------
Text between << and >> (full or partial card name) is searched in the card DB:
one match → card image; several → a "Show matches" button that opens the
/cardsearch results list and picker privately (ephemeral) for whoever clicks.
"""
import base64
import io
import logging
import re

import discord
from discord.ext import commands

import bot.services.guild_settings as gs
from bot.services.deck_decoder import DeckDecoder
from bot.services.hs_json_client import HSJsonClient
from bot.services.image_generator import ImageGenerator
from bot.commands.deck_commands import build_simple_deck_text
from bot.commands.search_commands import reply_card_lookup

log = logging.getLogger(__name__)

# Hearthstone deck codes are base64url strings, typically 60–200 chars,
# always starting with "AAE" (the encoded header byte sequence).
# Note: trailing \b cannot be used after '=' (non-word char); (?!\w) is used instead.
_DECK_RE = re.compile(r'\bAAE[A-Za-z0-9+/]{20,}={0,2}(?!\w)')

# <<card name>> lookups. At least 2 characters, so <<a>> doesn't list half the DB.
_CARD_QUERY_RE = re.compile(r'<<\s*([^<>\n]{2,60}?)\s*>>')
# Cap per message so one message can't make the bot post a wall of replies.
_MAX_CARD_QUERIES = 3


def extract_card_queries(text: str) -> list[str]:
    """Distinct <<card name>> queries in *text*, in order, capped at _MAX_CARD_QUERIES."""
    queries: list[str] = []
    for match in _CARD_QUERY_RE.findall(text):
        query = " ".join(match.split())
        if len(query) >= 2 and query.lower() not in (q.lower() for q in queries):
            queries.append(query)
    return queries[:_MAX_CARD_QUERIES]


def _looks_like_deck_code(token: str) -> bool:
    """Return True if the token survives base64 decoding without errors."""
    try:
        # Pad to multiple of 4
        padded = token + "=" * (-len(token) % 4)
        base64.b64decode(padded, validate=True)
        return True
    except Exception:
        return False


class AutoDetectCog(commands.Cog):
    """Passively monitors channels for Hearthstone deck codes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.hs_client = HSJsonClient()
        self.decoder = DeckDecoder(self.hs_client)
        self.image_gen = ImageGenerator(self.hs_client)

    async def _reply_deck_image(self, message: discord.Message, token: str) -> bool:
        """Decode *token* and reply with a deck image. Returns True on success."""
        pending = await message.reply("⏳ Processing deck, please wait…", mention_author=False)

        try:
            deck = await self.decoder.decode(token)
        except ValueError as exc:
            await pending.edit(content=f"❌ Could not decode deck code: {exc}")
            return False
        except Exception:
            log.warning("Failed to decode deck from bot-mention, token=%.40s", token, exc_info=True)
            await pending.edit(content="❌ Something went wrong while decoding the deck.")
            return False

        try:
            image_bytes = await self.image_gen.generate_deck_image(deck)
            file = discord.File(fp=image_bytes, filename="deck.png")
            await message.reply(
                content=f"**{deck.hero_class}** — {deck.format_label}  ·  {deck.total_cards} cards",
                file=file,
                mention_author=False,
            )
            try:
                await pending.delete()
            except Exception:
                pass
            return True
        except Exception:
            log.warning("Failed to send deck image reply in channel %s", message.channel.id, exc_info=True)
            try:
                await pending.edit(content="❌ Failed to send the deck image.")
            except Exception:
                log.warning("Also failed to edit pending message in channel %s", message.channel.id)
            return False

    async def _reply_auto_deck_image(self, message: discord.Message, deck) -> bool:
        """
        Auto-detect reply in deck-image mode: same output as /deckimage.
        Returns False on failure so the caller falls back to the card list.
        """
        try:
            image_bytes = await self.image_gen.generate_deck_image(deck)
            file = discord.File(fp=image_bytes, filename="deck.png")
            await message.reply(
                content=f"**{deck.hero_class}** — {deck.format_label}  ·  {deck.total_cards} cards",
                file=file,
                mention_author=False,
            )
            return True
        except Exception:
            log.warning(
                "Auto-detect deck image failed in channel %s, falling back to card list",
                message.channel.id, exc_info=True,
            )
            return False

    async def _reply_card_lookups(self, message: discord.Message, queries: list[str]) -> None:
        for query in queries:
            try:
                await reply_card_lookup(self.hs_client, message, query)
            except Exception:
                log.warning(
                    "Card lookup failed in channel %s, query=%r",
                    message.channel.id, query, exc_info=True,
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore bot messages
        if message.author.bot:
            return

        card_queries = extract_card_queries(message.content)

        # ── Bot-mention path ──────────────────────────────────────────────────
        # When the bot is @tagged AND a deck code or <<card name>> is present,
        # always reply — regardless of guild settings or channel scope.
        if self.bot.user in message.mentions:
            candidates = _DECK_RE.findall(message.content)
            for token in candidates:
                if _looks_like_deck_code(token):
                    await self._reply_deck_image(message, token)
                    break
            await self._reply_card_lookups(message, card_queries)
            # Handled (or nothing to do) — do not fall through to auto-detect
            return

        # ── Auto-detect path ─────────────────────────────────────────────────
        # Ignore DMs for the passive auto-detect feature
        if message.guild is None:
            return

        cfg = await gs.load(message.guild.id)

        # Feature disabled for this server
        if not cfg.auto_detect:
            return

        # Channel scope check
        if not cfg.all_channels and message.channel.id not in cfg.monitored_channels:
            return

        await self._reply_card_lookups(message, card_queries)

        # Step 1 — regex scan
        candidates = _DECK_RE.findall(message.content)
        if not candidates:
            return

        for token in candidates:
            # Step 2 — base64 sanity check
            if not _looks_like_deck_code(token):
                continue

            # Step 3 — full deck parse
            try:
                deck = await self.decoder.decode(token)
            except Exception:
                continue

            log.info("auto-detect deck code in guild=%s channel=%s user=%s display=%s code=%.40s", message.guild.id, message.channel.id, message.author, cfg.deck_display, token)
            # Step 4 — reply in the same channel, as the guild's /botadmin decktype says
            if cfg.deck_display == gs.DECK_DISPLAY_IMAGE and await self._reply_auto_deck_image(message, deck):
                break
            text = build_simple_deck_text(deck, token)
            try:
                await message.reply(text, mention_author=False)
            except discord.HTTPException:
                log.warning("Failed to reply in channel %s", message.channel.id)

            # Only respond to the first valid deck code per message
            break


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoDetectCog(bot))
