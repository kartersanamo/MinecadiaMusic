"""Register analytics cogs on this bot."""
from __future__ import annotations

from discord.ext import commands


async def register_command_tracking(bot: commands.Bot) -> None:
    if "AnalyticsTracking" in bot.cogs:
        return
    from core.analytics.command_usage import setup as setup_command_usage

    await setup_command_usage(bot)
