"""Global Discord event logging (interactions, voice, lifecycle)."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from core.action_log import interaction_context, log_action


def install_event_logging(
    bot: commands.Bot,
    *,
    log_events: logging.Logger,
) -> None:
    """Register listeners that log major bot activity to console and file."""

    @bot.event
    async def on_interaction(interaction: discord.Interaction) -> None:
        itype = interaction.type
        if itype is discord.InteractionType.application_command:
            cmd = interaction.command
            cmd_name = None
            if cmd is not None:
                cmd_name = getattr(cmd, "qualified_name", None) or getattr(cmd, "name", None)
            log_action(
                log_events,
                "interaction.command",
                **interaction_context(interaction),
                command=cmd_name or "unknown",
            )
            return

        if itype is discord.InteractionType.component:
            data = interaction.data if isinstance(interaction.data, dict) else {}
            log_action(
                log_events,
                "interaction.component",
                **interaction_context(interaction),
                component_type=data.get("component_type"),
            )
            return

        if itype is discord.InteractionType.modal_submit:
            data = interaction.data if isinstance(interaction.data, dict) else {}
            log_action(
                log_events,
                "interaction.modal",
                **interaction_context(interaction),
                custom_id=data.get("custom_id"),
            )
            return

        log_action(
            log_events,
            f"interaction.{itype.name.lower()}",
            **interaction_context(interaction),
        )

    @bot.event
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if bot.user is None or member.id != bot.user.id:
            return
        before_ch = before.channel.id if before.channel else None
        after_ch = after.channel.id if after.channel else None
        if before_ch == after_ch:
            return
        log_action(
            log_events,
            "voice.bot_moved",
            guild_id=member.guild.id,
            guild=member.guild.name,
            from_channel=before_ch,
            to_channel=after_ch,
        )

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        log_action(
            log_events,
            "guild.join",
            guild_id=guild.id,
            guild=guild.name,
            member_count=guild.member_count,
        )

    @bot.event
    async def on_guild_remove(guild: discord.Guild) -> None:
        log_action(
            log_events,
            "guild.leave",
            guild_id=guild.id,
            guild=guild.name,
        )

    log_events.info("Global event logging installed")
