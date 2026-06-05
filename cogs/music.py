from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.errors.exceptions import UserFacingError
from core.errors.logging import log_exception
from core.config import ConfigManager
from core.loggers import log_commands
from ui.views.music_panel_layout_view import MusicPanelLayoutView
from ui.views.music_panel_support import MusicPanelState


def _embed(title: str, description: str, bot: commands.Bot) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.from_str(ConfigManager.get("EMBED_COLOR")),
    )
    logo_url = bot.app.embeds.get_logo_url(ConfigManager.get("LOGO"))
    embed.set_footer(text=ConfigManager.get("FOOTER"), icon_url=logo_url)
    return embed


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.guild_only()
    @app_commands.command(name="music", description="Open the music player panel")
    async def music(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            if not interaction.guild:
                raise UserFacingError("Music commands can only be used in a server.")
            session = self.bot.app.music.get_session(interaction.guild.id)
            url = session.refresh_panel(interaction.user.id)
            state = MusicPanelState(
                session=session,
                bot=self.bot,
                owner_id=interaction.user.id,
                panel_url=url,
                guild=interaction.guild,
                notice="Use the controls below to play and manage music.",
            )
            view = MusicPanelLayoutView(interaction, state)
            msg = await interaction.followup.send(
                content=None,
                embed=None,
                view=view,
                wait=True,
            )
            session.bind_panel_message(msg, interaction.user.id)
        except UserFacingError as exc:
            await interaction.followup.send(
                embed=_embed("Music", exc.user_message, self.bot),
                ephemeral=True,
            )
        except Exception as exc:
            log_exception(log_commands, exc, bot_name="Music", interaction=interaction)
            await interaction.followup.send(
                embed=_embed("Music", "Something went wrong. Please try again later.", self.bot),
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    bot.tree.remove_command("music")
    await bot.add_cog(Music(bot))
