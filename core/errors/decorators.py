"""Decorators for safe UI callbacks and tasks."""
from __future__ import annotations

import functools
import logging
from typing import Callable, TypeVar

import discord

from core.errors.interactions import safe_reply
from core.errors.logging import log_exception
from core.errors.messages import external_service_message

F = TypeVar("F", bound=Callable)


def safe_interaction(
    logger: logging.Logger,
    *,
    bot_name: str | None = None,
    user_message: str | None = None,
    component: str | None = None,
) -> Callable[[F], F]:
    """Wrap a view/modal callback: log failures and reply ephemerally."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            try:
                return await func(self, interaction, *args, **kwargs)
            except Exception as exc:
                msg = user_message or external_service_message(exc)
                log_exception(
                    logger,
                    exc,
                    bot_name=bot_name,
                    interaction=interaction,
                    component=component or func.__name__,
                )
                await safe_reply(interaction, content=f"`❌` {msg}", ephemeral=True)

        return wrapper  # type: ignore

    return decorator
