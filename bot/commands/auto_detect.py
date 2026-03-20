"""
Automatic deck code detection — listens to messages and responds
when a valid Hearthstone deck string is found.

Detection pipeline
------------------
1. Regex scan  — find base64-like token(s) in message text
2. Base64 test — must decode cleanly to bytes
3. Deck parse  — hearthstone library must parse it as a valid deck
4. Reply       — same format as /deck command
"""
import base64
import logging
import re

import discord
from discord.ext import commands

import bot.services.guild_settings as gs
from bot.services.deck_decoder import DeckDecoder
from bot.services.hs_json_client import HSJsonClient
from bot.commands.deck_commands import build_simple_deck_text

log = logging.getLogger(__name__)

# Hearthstone deck codes are base64url strings, typically 60–200 chars,
# always starting with "AAE" (the encoded header byte sequence).
_DECK_RE = re.compile(r'\bAAE[A-Za-z0-9+/]{20,}={0,2}\b')


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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore DMs, bot messages, and messages with no guild
        if message.guild is None or message.author.bot:
            return

        cfg = await gs.load(message.guild.id)

        # Feature disabled for this server
        if not cfg.auto_detect:
            return

        # Channel scope check
        if not cfg.all_channels and message.channel.id not in cfg.monitored_channels:
            return

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

            # Step 4 — reply in the same channel
            text = build_simple_deck_text(deck, token)
            try:
                await message.reply(text, mention_author=False)
            except discord.HTTPException:
                log.warning("Failed to reply in channel %s", message.channel.id)

            # Only respond to the first valid deck code per message
            break


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoDetectCog(bot))
