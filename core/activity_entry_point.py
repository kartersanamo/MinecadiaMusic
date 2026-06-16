"""Discord Activity Entry Point command registration and handling."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from core.action_log import log_action

import aiohttp
import discord

if TYPE_CHECKING:
    from discord.ext import commands

log = logging.getLogger("Tasks")

PRIMARY_ENTRY_POINT = 4
APP_HANDLER_LAUNCH_ACTIVITY = 3

ENTRY_POINT_PAYLOAD: dict[str, Any] = {
    "name": "launch",
    "description": "Launch the Minecadia Music dashboard",
    "type": PRIMARY_ENTRY_POINT,
    "handler": APP_HANDLER_LAUNCH_ACTIVITY,
    "integration_types": [0, 1],
    "contexts": [0, 1, 2],
}


def _entry_point_command_type(interaction: discord.Interaction) -> int | None:
    if interaction.type is not discord.InteractionType.application_command:
        return None
    data = interaction.data
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get("type")
    return getattr(data, "type", None)


_ACTIVITY_UNSUPPORTED_PLATFORM_CODES = frozenset({50230, 50231})


def activity_launch_error_message(
    exc: discord.HTTPException,
    *,
    panel_url: str | None = None,
) -> str:
    code = getattr(exc, "code", None)
    if code in _ACTIVITY_UNSUPPORTED_PLATFORM_CODES:
        lines = [
            "The in-Discord dashboard is not available on your device yet.",
            "Use **Open in browser** on the `/music` panel instead.",
        ]
        if panel_url:
            lines.append(panel_url)
        return "\n".join(lines)
    return "Could not launch the music dashboard. Run **`/music`** in this server first."


async def launch_music_activity(
    interaction: discord.Interaction,
    *,
    panel_url: str | None = None,
) -> bool:
    """Respond to an interaction by launching the Discord Activity. Returns True on success."""
    try:
        await interaction.response.launch_activity()
        log_action(
            log,
            "activity.launch_ok",
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
        )
        log.info(
            "Launched Activity for user %s in guild %s",
            interaction.user.id,
            interaction.guild_id,
        )
        return True
    except discord.HTTPException as exc:
        log_action(
            log,
            "activity.launch_failed",
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            code=getattr(exc, "code", None),
            error=str(exc)[:160],
        )
        log.warning(
            "Activity launch failed for user %s (code=%s): %s",
            interaction.user.id,
            getattr(exc, "code", None),
            exc,
        )
        message = activity_launch_error_message(exc, panel_url=panel_url)
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
        return False


async def _respond_to_entry_point(interaction: discord.Interaction) -> None:
    """Launch the Activity iframe for App Launcher Entry Point (type 4) interactions."""
    await launch_music_activity(interaction)


def install_activity_entry_point(bot: "commands.Bot") -> None:
    """Handle App Launcher Entry Point interactions before the command tree misroutes them.

    discord.py 2.7 has no ``@tree.interaction_check`` decorator — assigning the coroutine
    directly is required. We also patch ``CommandTree._call`` because type 4 is not a valid
    ``AppCommandType`` and the library routes it to the context-menu handler otherwise.
    """

    async def _launch_from_entry_point(interaction: discord.Interaction) -> bool:
        if _entry_point_command_type(interaction) != PRIMARY_ENTRY_POINT:
            return True
        await _respond_to_entry_point(interaction)
        return False

    bot.tree.interaction_check = _launch_from_entry_point

    original_call = bot.tree._call

    async def _call_with_entry_point(interaction: discord.Interaction) -> None:
        if _entry_point_command_type(interaction) == PRIMARY_ENTRY_POINT:
            await _respond_to_entry_point(interaction)
            return
        await original_call(interaction)

    bot.tree._call = _call_with_entry_point


async def ensure_activity_entry_point(bot: "commands.Bot") -> None:
    """Register or repair the global Entry Point command for the App Launcher."""
    app_id = bot.application_id
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not app_id or not token:
        log.warning("Skipping Activity Entry Point setup — missing application id or token")
        return

    url = f"https://discord.com/api/v10/applications/{app_id}/commands"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as http:
        async with http.get(url, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error("Failed to list global commands for Entry Point setup (%s): %s", resp.status, body)
                return
            commands = await resp.json()

        entry = next((cmd for cmd in commands if cmd.get("type") == PRIMARY_ENTRY_POINT), None)
        if entry is None:
            async with http.post(url, headers=headers, json=ENTRY_POINT_PAYLOAD) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    log.error("Failed to create Activity Entry Point (%s): %s", resp.status, body)
                    return
                created = await resp.json()
                log.info(
                    "Created Activity Entry Point command %s (handler=%s)",
                    created.get("id"),
                    created.get("handler"),
                )
                return

        if entry.get("handler") != APP_HANDLER_LAUNCH_ACTIVITY:
            patch_url = f"{url}/{entry['id']}"
            async with http.patch(patch_url, headers=headers, json=ENTRY_POINT_PAYLOAD) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Failed to update Activity Entry Point (%s): %s", resp.status, body)
                    return
                updated = await resp.json()
                log.info(
                    "Updated Activity Entry Point command %s (handler=%s)",
                    updated.get("id"),
                    updated.get("handler"),
                )
                return

        log.info("Activity Entry Point command already configured (%s)", entry.get("id"))
