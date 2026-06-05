"""Slash command usage — load on every bot that serves app commands."""
from __future__ import annotations

import discord
from discord.ext import commands

from core.analytics import logger as analytics


class CommandUsage(commands.Cog):
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.application_command:
            return
        if not interaction.command:
            return
        analytics.record_command(
            str(interaction.client.user.id),
            interaction.command.qualified_name,
        )


async def setup(client: commands.Bot) -> None:
    if any(isinstance(c, CommandUsage) for c in client.cogs.values()):
        return
    await client.add_cog(CommandUsage())
