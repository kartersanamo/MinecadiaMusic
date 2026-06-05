from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
import wavelink

from core.config import ConfigManager


class MusicSearchView(discord.ui.View):
    def __init__(
        self,
        session,
        tracks: list[wavelink.Playable],
        requester_id: int,
        bot,
        *,
        on_track_added: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__(timeout=120)
        self.session = session
        self._tracks = tracks
        self.requester_id = requester_id
        self.bot = bot
        self.on_track_added = on_track_added
        options = []
        for i, t in enumerate(tracks[:25]):
            label = (t.title or "Unknown")[:100]
            desc = (t.author or "")[:100]
            options.append(
                discord.SelectOption(label=label, description=desc, value=str(i))
            )
        select = discord.ui.Select(
            placeholder="Choose a track…",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        idx = int(interaction.data.get("values")[0])
        track = self._tracks[idx]
        track.extras = {"requester_id": self.requester_id}
        started = await self.session.add_tracks([track], self.requester_id)
        embed = discord.Embed(
            description=f"{'Now playing' if started[1] else 'Queued'} **{track.title}**",
            color=discord.Color.from_str(ConfigManager.get("EMBED_COLOR")),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if self.on_track_added:
            await self.on_track_added()
        self.stop()
