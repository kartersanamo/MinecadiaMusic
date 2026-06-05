"""Global Discord and asyncio error handlers."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.errors.interactions import safe_reply
from core.errors.logging import log_exception
from core.errors.messages import user_message_for


def install_error_handlers(
    bot: commands.Bot,
    *,
    bot_name: str,
    log_commands: logging.Logger,
    log_tasks: logging.Logger,
) -> None:
    """Register tree, prefix command, and asyncio task error handlers on the bot."""

    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        message = user_message_for(error)
        log_exception(
            log_commands,
            error,
            bot_name=bot_name,
            interaction=interaction,
            component="slash_command",
        )
        await safe_reply(interaction, content=f"`❌` {message}", ephemeral=True)

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        message = user_message_for(error)
        log_exception(
            log_commands,
            error,
            bot_name=bot_name,
            extra={
                "user_id": ctx.author.id,
                "guild_id": getattr(ctx.guild, "id", None),
                "channel_id": getattr(ctx.channel, "id", None),
                "command": ctx.command.name if ctx.command else None,
            },
            component="prefix_command",
        )
        try:
            await ctx.send(f"`❌` {message}", delete_after=15)
        except discord.HTTPException:
            pass

    def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        msg = context.get("message", "Unhandled asyncio exception")
        if exc is not None:
            log_exception(log_tasks, exc, bot_name=bot_name, component="asyncio_task")
        else:
            log_tasks.error(f"[{bot_name}] asyncio: {msg}")

    bot._minecadia_asyncio_exception_handler = _asyncio_exception_handler  # type: ignore[attr-defined]
    log_tasks.info(f"[{bot_name}] Discord error handlers installed")


def install_asyncio_exception_handler(
    bot: commands.Bot,
    *,
    log_tasks: logging.Logger,
    bot_name: str,
) -> None:
    """Call from bot setup_hook (async context) to catch unhandled task exceptions."""
    handler = getattr(bot, "_minecadia_asyncio_exception_handler", None)
    if handler is None:

        def _fallback(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            exc = context.get("exception")
            if exc is not None:
                log_exception(log_tasks, exc, bot_name=bot_name, component="asyncio_task")
            else:
                log_tasks.error(f"[{bot_name}] asyncio: {context.get('message', 'unknown')}")

        handler = _fallback

    asyncio.get_running_loop().set_exception_handler(handler)
