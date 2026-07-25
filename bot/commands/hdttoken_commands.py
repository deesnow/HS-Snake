"""
RankTrackerAPI token slash command: /hdttoken.

Lets a user generate a bearer token for the Hearthstone Deck Tracker
RankTrackerPlugin, delivered by DM. Regenerating an existing token requires
confirmation, since it immediately invalidates the old one.
"""
import hashlib
import logging
import secrets

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.db import get_db

log = logging.getLogger(__name__)


class RegenerateTokenView(discord.ui.View):
    """Confirm/cancel prompt shown when the user already has an active token."""

    def __init__(self, cog: "HdtTokenCommands", discord_id: str, existing_token_id: int) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.discord_id = discord_id
        self.existing_token_id = existing_token_id

    @discord.ui.button(label="Regenerate", style=discord.ButtonStyle.danger)
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await self.cog.issue_token(
            interaction, self.discord_id, revoke_id=self.existing_token_id, respond_via_edit=True
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="_Cancelled — your existing token is unchanged._", view=None
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]


class HdtTokenCommands(commands.Cog):
    """RankTrackerAPI token issuance for the HDT plugin."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def issue_token(
        self,
        interaction: discord.Interaction,
        discord_id: str,
        revoke_id: int | None,
        respond_via_edit: bool,
    ) -> None:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        # DM first: if the user can't receive it, don't touch the DB at all.
        try:
            await interaction.user.send(
                "🔑 Your RankTrackerAPI token for the Hearthstone Deck Tracker plugin:\n"
                f"```\n{token}\n```\n"
                "Paste this into the plugin's settings as the Bearer token. Keep it secret — "
                "anyone with this token can upload match data as you. Run `/hdttoken` again "
                "any time to regenerate it (this immediately invalidates the old one)."
            )
        except discord.Forbidden:
            msg = (
                "❌ I couldn't DM you — please enable **Allow direct messages from server "
                "members** in your Privacy Settings and run `/hdttoken` again."
            )
            if respond_via_edit:
                await interaction.response.edit_message(content=msg, view=None)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        try:
            async with get_db() as conn:
                async with conn.transaction():
                    if revoke_id is not None:
                        await conn.execute(
                            "UPDATE rank_api_tokens SET revoked_at = now() WHERE id = $1", revoke_id
                        )
                    await conn.execute(
                        "INSERT INTO rank_api_tokens (discord_id, token_hash, label) VALUES ($1, $2, $3)",
                        discord_id, token_hash, "hdttoken",
                    )
        except Exception:
            log.exception("Failed to persist rank_api_tokens row for discord_id=%s", discord_id)
            msg = "⚠️ Sent you a token, but saving it failed — please run `/hdttoken` again before using it."
            if respond_via_edit:
                await interaction.response.edit_message(content=msg, view=None)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        log.info("/hdttoken issued new token discord_id=%s revoked_id=%s", discord_id, revoke_id)
        msg = "✅ Check your DMs — I've sent you a new RankTrackerAPI token."
        if respond_via_edit:
            await interaction.response.edit_message(content=msg, view=None)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="hdttoken",
        description="Generate or regenerate your Hearthstone Deck Tracker plugin API token.",
    )
    async def hdttoken(self, interaction: discord.Interaction) -> None:
        discord_id = str(interaction.user.id)
        log.info("/hdttoken user=%s", interaction.user)
        try:
            async with get_db() as conn:
                existing = await conn.fetchrow(
                    "SELECT id FROM rank_api_tokens WHERE discord_id = $1 AND revoked_at IS NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    discord_id,
                )

            if existing is None:
                await self.issue_token(interaction, discord_id, revoke_id=None, respond_via_edit=False)
                return

            view = RegenerateTokenView(self, discord_id, existing["id"])
            await interaction.response.send_message(
                "⚠️ You already have an active RankTrackerAPI token. Generating a new one will "
                "immediately invalidate the old one. Regenerate?",
                view=view,
                ephemeral=True,
            )
        except Exception:
            log.exception("Unexpected error in /hdttoken")
            await interaction.response.send_message("❌ Something went wrong. Please try again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HdtTokenCommands(bot))
