"""
/botadmin slash-command group — per-guild bot configuration.

All subcommands require the user to hold the configured admin role
(or be the server owner / have Administrator permission).
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

import bot.services.guild_settings as gs

log = logging.getLogger(__name__)


def _is_admin(interaction: discord.Interaction, settings) -> bool:
    """Return True if the invoking member is allowed to run admin commands."""
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    # Server owner always allowed
    if member.id == interaction.guild.owner_id:
        return True
    # Administrator permission always allowed
    if member.guild_permissions.administrator:
        return True
    # Configured admin role
    if settings.admin_role_id and any(r.id == settings.admin_role_id for r in member.roles):
        return True
    return False


class AdminCommands(commands.Cog):
    """Bot administration commands (per-guild settings)."""

    admin = app_commands.Group(
        name="botadmin",
        description="Configure HS-Snake bot settings for this server.",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ── /botadmin setrole ─────────────────────────────────────────────

    @admin.command(name="setrole", description="Set the role that is allowed to manage bot settings.")
    @app_commands.describe(role="The bot-admin role")
    async def setrole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only server administrators can set the admin role.", ephemeral=True)
            return
        await gs.set_admin_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            f"✅ Bot admin role set to **{role.name}**.", ephemeral=True
        )

    # ── /botadmin autodetect ──────────────────────────────────────────

    @admin.command(name="autodetect", description="Enable or disable automatic deck code detection.")
    @app_commands.describe(enabled="Turn auto-detection on or off")
    @app_commands.choices(enabled=[
        app_commands.Choice(name="on",  value=1),
        app_commands.Choice(name="off", value=0),
    ])
    async def autodetect(self, interaction: discord.Interaction, enabled: int) -> None:
        settings = await gs.load(interaction.guild_id)
        if not _is_admin(interaction, settings):
            await interaction.response.send_message("❌ You don't have permission to change bot settings.", ephemeral=True)
            return
        await gs.set_auto_detect(interaction.guild_id, bool(enabled))
        state = "**enabled**" if enabled else "**disabled**"
        await interaction.response.send_message(
            f"✅ Automatic deck detection is now {state}.", ephemeral=True
        )

    # ── /botadmin channels ────────────────────────────────────────────

    @admin.command(name="allchannels", description="Monitor all text channels (overrides individual channel settings).")
    @app_commands.describe(enabled="on = all channels, off = only configured channels")
    @app_commands.choices(enabled=[
        app_commands.Choice(name="on",  value=1),
        app_commands.Choice(name="off", value=0),
    ])
    async def allchannels(self, interaction: discord.Interaction, enabled: int) -> None:
        settings = await gs.load(interaction.guild_id)
        if not _is_admin(interaction, settings):
            await interaction.response.send_message("❌ You don't have permission to change bot settings.", ephemeral=True)
            return
        await gs.set_all_channels(interaction.guild_id, bool(enabled))
        state = "**all channels**" if enabled else "**configured channels only**"
        await interaction.response.send_message(
            f"✅ Deck detection scope set to {state}.", ephemeral=True
        )

    @admin.command(name="addchannel", description="Add a channel to the deck-detection watch list.")
    @app_commands.describe(channel="Text channel to monitor")
    async def addchannel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        settings = await gs.load(interaction.guild_id)
        if not _is_admin(interaction, settings):
            await interaction.response.send_message("❌ You don't have permission to change bot settings.", ephemeral=True)
            return
        await gs.add_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ {channel.mention} added to the watch list.", ephemeral=True
        )

    @admin.command(name="removechannel", description="Remove a channel from the deck-detection watch list.")
    @app_commands.describe(channel="Text channel to remove")
    async def removechannel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        settings = await gs.load(interaction.guild_id)
        if not _is_admin(interaction, settings):
            await interaction.response.send_message("❌ You don't have permission to change bot settings.", ephemeral=True)
            return
        await gs.remove_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ {channel.mention} removed from the watch list.", ephemeral=True
        )

    # ── /botadmin status ──────────────────────────────────────────────

    @admin.command(name="status", description="Show the current bot configuration for this server.")
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = await gs.load(interaction.guild_id)
        if not _is_admin(interaction, cfg):
            await interaction.response.send_message("❌ You don't have permission to view bot settings.", ephemeral=True)
            return

        admin_role = f"<@&{cfg.admin_role_id}>" if cfg.admin_role_id else "*(not set — Administrators only)*"
        detect_state = "✅ Enabled" if cfg.auto_detect else "❌ Disabled"
        scope = "All channels" if cfg.all_channels else (
            ", ".join(f"<#{c}>" for c in cfg.monitored_channels) or "*(none configured)*"
        )

        embed = discord.Embed(title="HS-Snake — Server Configuration", colour=0x1A1A2E)
        embed.add_field(name="Admin Role",         value=admin_role,    inline=False)
        embed.add_field(name="Auto-Detect",        value=detect_state,  inline=True)
        embed.add_field(name="Monitored Channels", value=scope,         inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))
