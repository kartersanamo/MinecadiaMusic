import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)

from discord.ext import commands
from discord import app_commands
import discord
from dotenv import load_dotenv
from core.app import BotApp
from core.config import ConfigManager
from core.decorators import task
from core.loggers import log_commands, log_tasks

load_dotenv()

from core.activity_entry_point import ensure_activity_entry_point, install_activity_entry_point
from core.errors.setup import wire_bot

COG_FILES = [file.split(".")[0].title() for file in os.listdir("cogs/") if file.endswith(".py")]


class Client(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=".", intents=discord.Intents().all())
        wire_bot(self, bot_name="Music", log_commands=log_commands, log_tasks=log_tasks)
        install_activity_entry_point(self)

    @task("Setup Cogs")
    async def setup_cogs(self):
        for ext in COG_FILES:
            log_tasks.info(f"Loaded cog {ext}.py")
            await self.load_extension("cogs." + ext.lower())
        from core.analytics.register import register_command_tracking

        await register_command_tracking(self)

    @task("Update Presence")
    async def update_presence(self):
        presence = ConfigManager.get("PRESENCE")
        await client.change_presence(activity=discord.Game(name=presence))
        log_tasks.info(f"Updated the bot's presence to {presence}")

    @task("Remove Help")
    async def remove_help(self):
        client.remove_command("help")

    @task("Sync Command Tree")
    async def sync_command_tree(self) -> list[discord.app_commands.AppCommand]:
        from core.guild_command_sync import sync_guild_commands

        return await sync_guild_commands(
            self,
            log=log_tasks,
            also_sync_global=False,
            clear_global_after_guild=True,
        )

    @task("Setup Lavalink")
    async def setup_lavalink(self):
        self.app = BotApp.from_bot(self)
        try:
            await self.app.music.connect_lavalink()
            await self.app.music.register_events()
        except Exception as exc:
            log_tasks.error("Lavalink connection failed (music disabled until fixed): %s", exc)

    @task("Start Music HTTP")
    async def setup_music_http(self):
        if not self.app.music._lavalink_ready:
            log_tasks.warning("Skipping music HTTP — Lavalink not connected")
            return
        from assets.music_http import start_music_http

        await start_music_http(self)

    @task("Setup Hook")
    async def setup_hook(self):
        from core.errors.setup import wire_bot_async_setup

        await wire_bot_async_setup(self, bot_name="Music", log_tasks=log_tasks)
        await self.setup_lavalink()
        await self.setup_cogs()
        await self.setup_music_http()

    @task("Logging in")
    async def on_ready(self):
        from ui.views.music_panel_layout_view import register_persistent_music_panel

        register_persistent_music_panel(self)
        try:
            await self.app.music.restore_panels()
        except Exception as exc:
            log_tasks.error("Failed to restore music panels: %s", exc)
        await ensure_activity_entry_point(self)
        await self.update_presence()
        await self.remove_help()
        await self.sync_command_tree()
        log_tasks.info(f"Logged in as {client.user} ({client.user.id})")


client = Client()


@task("Music Reload Command", True)
async def music_reload_command(interaction: discord.Interaction, cog: str):
    if interaction.guild is None:
        return await interaction.response.send_message(
            content="Commands cannot be ran in DMs!", ephemeral=True
        )
    if cog not in COG_FILES:
        await interaction.response.send_message(f"Invalid cog name **{cog}.py**", ephemeral=True)
        return
    await client.reload_extension(f"cogs.{cog.lower()}")
    synced = await client.sync_command_tree()
    await interaction.response.send_message(
        f"Successfully reloaded **{cog}.py** and synced **{len(synced)}** slash commands.",
        ephemeral=True,
    )


async def cog_autocomplete(_: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=cog, value=cog)
        for cog in COG_FILES
        if current.lower() in cog.lower()
    ]


@client.tree.command(name="music-reload", description="Reloads a Cog Class")
@app_commands.autocomplete(cog=cog_autocomplete)
async def musicreload(interaction: discord.Interaction, cog: str):
    await music_reload_command(interaction, cog)


TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Set DISCORD_TOKEN in .env")

if __name__ == "__main__":
    client.run(TOKEN)
