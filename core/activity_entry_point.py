"""Discord Activity Entry Point command registration and handling."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

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


def install_activity_entry_point(bot: "commands.Bot") -> None:
    """Handle App Launcher Entry Point interactions before the command tree misroutes them."""

    @bot.tree.interaction_check
    async def _launch_from_entry_point(interaction: discord.Interaction) -> bool:
        if _entry_point_command_type(interaction) != PRIMARY_ENTRY_POINT:
            return True
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException:
            log.exception("Activity Entry Point launch failed")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Could not launch the music dashboard. Run **`/music`** in this server first.",
                    ephemeral=True,
                )
        return False


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
