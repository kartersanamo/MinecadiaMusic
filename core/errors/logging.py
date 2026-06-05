"""Structured exception logging for all bots."""
from __future__ import annotations

import logging
from typing import Any, Optional

import discord

from core.errors.exceptions import UserFacingError


def _interaction_context(interaction: Optional[discord.Interaction]) -> dict[str, Any]:
    if interaction is None:
        return {}
    ctx: dict[str, Any] = {
        "user_id": getattr(interaction.user, "id", None),
        "guild_id": getattr(interaction.guild, "id", None),
        "channel_id": getattr(interaction.channel, "id", None),
    }
    cmd = interaction.command
    if cmd is not None:
        ctx["command"] = getattr(cmd, "qualified_name", None) or getattr(cmd, "name", None)
    return {k: v for k, v in ctx.items() if v is not None}


def log_exception(
    logger: logging.Logger,
    exc: BaseException,
    *,
    bot_name: str | None = None,
    interaction: Optional[discord.Interaction] = None,
    component: str | None = None,
    extra: Optional[dict[str, Any]] = None,
    level: int = logging.ERROR,
) -> None:
    parts: list[str] = []
    if bot_name:
        parts.append(f"[{bot_name}]")
    if component:
        parts.append(f"{component}:")
    if isinstance(exc, UserFacingError) and exc.log_message:
        parts.append(exc.log_message)
    else:
        parts.append(str(exc))

    ctx = _interaction_context(interaction)
    if extra:
        ctx.update(extra)
    if ctx:
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        parts.append(f"({ctx_str})")

    logger.log(level, " ".join(parts), exc_info=exc if level >= logging.ERROR else None)
