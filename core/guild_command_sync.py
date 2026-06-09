"""Guild-scoped slash command sync for Minecadia bots."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext import commands

_logger = logging.getLogger(__name__)


def resolve_guild_id(config_guild_id: int | str | None = None) -> int | None:
    raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not raw.isdigit() and config_guild_id is not None:
        raw = str(config_guild_id).strip()
    if raw.isdigit():
        return int(raw)
    return None


def format_command_names(commands_list: list[discord.app_commands.AppCommand]) -> str:
    parts: list[str] = []
    for command in commands_list:
        subs = getattr(command, "options", None) or []
        if subs:
            sub_names = ", ".join(getattr(o, "name", str(o)) for o in subs[:8])
            parts.append(f"{command.name}({sub_names})")
        else:
            parts.append(command.name)
    return ", ".join(parts)


async def _sync_global(bot: "commands.Bot", warn) -> list[discord.app_commands.AppCommand]:
    try:
        return await bot.tree.sync()
    except discord.HTTPException as exc:
        if exc.code == 50240:
            warn(
                "Global command sync incomplete — Discord Activities Entry Point "
                "must stay registered (50240)."
            )
            return []
        raise


async def sync_guild_commands(
    bot: "commands.Bot",
    *,
    config_guild_id: int | str | None = None,
    log=None,
    also_sync_global: bool = True,
    clear_global_after_guild: bool = False,
) -> list[discord.app_commands.AppCommand]:
    """Sync commands to the configured guild; optionally mirror or clear globals."""
    info = log.info if log else _logger.info
    warn = log.warning if log else _logger.warning

    guild_id = resolve_guild_id(config_guild_id)
    if guild_id is None:
        warn("DISCORD_GUILD_ID / GUILD_ID not set — falling back to global sync only")
        synced = await _sync_global(bot, warn)
        info("Globally synced %s commands: %s", len(synced), format_command_names(synced))
        return synced

    if not bot.application_id:
        warn("application_id not ready — skipping command sync")
        return []

    guild = discord.Object(id=guild_id)
    bot.tree.clear_commands(guild=guild)
    bot.tree.copy_global_to(guild=guild)
    guild_cmds = await bot.tree.sync(guild=guild)
    info(
        "Guild-synced %s commands to guild %s: %s",
        len(guild_cmds),
        guild_id,
        format_command_names(guild_cmds),
    )

    if clear_global_after_guild:
        try:
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            info("Cleared global slash commands")
        except discord.HTTPException as exc:
            if exc.code == 50240:
                warn(
                    "Skipped global command wipe — Discord Activities Entry Point "
                    "cannot be removed via bulk sync (50240)."
                )
            else:
                raise
        return guild_cmds

    if also_sync_global:
        synced = await _sync_global(bot, warn)
        info("Globally synced %s commands: %s", len(synced), format_command_names(synced))
        return synced

    return guild_cmds
