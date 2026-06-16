"""Structured action logging helpers used across the bot."""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

import discord

F = TypeVar("F", bound=Callable[..., Any])

_SENSITIVE_KEYS = frozenset({"token", "session_token", "password", "access_token", "code"})


def _safe_value(key: str, value: Any, *, max_len: int = 120) -> str:
    if key in _SENSITIVE_KEYS:
        return "<redacted>"
    if value is None:
        return "None"
    if isinstance(value, (discord.Member, discord.User)):
        name = getattr(value, "display_name", None) or getattr(value, "name", "?")
        return f"{value.id}({name})"
    if isinstance(value, discord.Guild):
        return f"{value.id}({value.name})"
    if isinstance(value, discord.abc.GuildChannel):
        return f"{value.id}({getattr(value, 'name', '?')})"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if len(value) > 5:
            return f"[{len(value)} items]"
        return str(value)[:max_len]
    text = str(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def format_context(**kwargs: Any) -> str:
    parts: list[str] = []
    for key, value in kwargs.items():
        if value is None:
            continue
        parts.append(f"{key}={_safe_value(key, value)}")
    return " ".join(parts)


def log_action(
    logger: logging.Logger,
    action: str,
    *,
    level: int = logging.INFO,
    **context: Any,
) -> None:
    ctx = format_context(**context)
    if ctx:
        logger.log(level, "%s | %s", action, ctx)
    else:
        logger.log(level, "%s", action)


def interaction_context(interaction: discord.Interaction) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "user_id": interaction.user.id,
        "user": getattr(interaction.user, "display_name", interaction.user.name),
    }
    if interaction.guild:
        ctx["guild_id"] = interaction.guild.id
        ctx["guild"] = interaction.guild.name
    if interaction.channel:
        ctx["channel_id"] = interaction.channel.id
    if interaction.command is not None:
        ctx["command"] = (
            getattr(interaction.command, "qualified_name", None)
            or getattr(interaction.command, "name", None)
        )
    data = interaction.data if isinstance(interaction.data, dict) else {}
    if custom_id := data.get("custom_id"):
        ctx["custom_id"] = custom_id
    if values := data.get("values"):
        ctx["values"] = values
    return ctx


def log_interaction(
    logger: logging.Logger,
    interaction: discord.Interaction,
    action: str,
    *,
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    ctx = interaction_context(interaction)
    ctx.update(extra)
    log_action(logger, action, level=level, **ctx)


def _summarize_music_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if "actor_id" in kwargs and kwargs["actor_id"] is not None:
        details["actor_id"] = kwargs["actor_id"]
    if "requester_id" in kwargs:
        details["requester_id"] = kwargs["requester_id"]
    if "query" in kwargs:
        details["query"] = kwargs["query"]
    if "level" in kwargs:
        details["level"] = kwargs["level"]
    if "mode" in kwargs:
        details["mode"] = kwargs["mode"]
    if "index" in kwargs:
        details["index"] = kwargs["index"]
    if "indices" in kwargs:
        details["indices"] = kwargs["indices"]
    if "from_index" in kwargs:
        details["from_index"] = kwargs["from_index"]
    if "to_index" in kwargs:
        details["to_index"] = kwargs["to_index"]
    if "playlist_title" in kwargs and kwargs["playlist_title"]:
        details["playlist_title"] = kwargs["playlist_title"]

    for arg in args:
        if isinstance(arg, discord.Member):
            details["member_id"] = arg.id
        elif isinstance(arg, discord.VoiceChannel):
            details["channel_id"] = arg.id
            details["channel"] = arg.name
        elif isinstance(arg, str) and "query" not in details:
            details["query"] = arg
        elif isinstance(arg, list):
            details["track_count"] = len(arg)

    return details


def _summarize_music_result(result: Any) -> str:
    if isinstance(result, tuple):
        return str(result)
    if isinstance(result, str):
        return result[:160]
    return type(result).__name__


def log_music_method(action: str | None = None) -> Callable[[F], F]:
    """Decorator for GuildMusicSession async actions."""

    def decorator(func: F) -> F:
        name = action or func.__name__

        @functools.wraps(func)
        async def wrapper(self, *args: Any, **kwargs: Any) -> Any:
            from core.loggers import log_music

            details = _summarize_music_args(args, kwargs)
            log_action(
                log_music,
                f"session.{name}.start",
                guild_id=self.guild_id,
                session_id=self.session_id[:8],
                **details,
            )
            try:
                result = await func(self, *args, **kwargs)
            except Exception as exc:
                from core.errors.exceptions import UserFacingError

                if isinstance(exc, UserFacingError):
                    log_action(
                        log_music,
                        f"session.{name}.rejected",
                        level=logging.INFO,
                        guild_id=self.guild_id,
                        session_id=self.session_id[:8],
                        reason=exc.user_message,
                    )
                else:
                    log_music.exception(
                        "session.%s.failed guild_id=%s session_id=%s",
                        name,
                        self.guild_id,
                        self.session_id[:8],
                    )
                raise

            log_action(
                log_music,
                f"session.{name}.ok",
                guild_id=self.guild_id,
                session_id=self.session_id[:8],
                result=_summarize_music_result(result),
            )
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
