"""Components V2 music player panel."""

from __future__ import annotations

import discord
from discord.enums import SeparatorSpacing

from core.config import ConfigManager
from core.errors.exceptions import UserFacingError
from services.music.permissions import can_control, can_queue, require_voice
from services.music.queue import LoopMode
from services.music.resolver import search_tracks
from ui.views.music_panel_support import (
    MusicPanelState,
    build_panel_markdown,
    check_panel_owner,
    edit_music_panel,
    refresh_panel_message,
    _accent_int,
)
from ui.views.music_search_view import MusicSearchView


def _voice_id(session) -> int | None:
    player = session.get_player()
    if player and player.channel:
        return player.channel.id
    return None


class _QueryModal(discord.ui.Modal, title="Play music"):
    query = discord.ui.TextInput(
        label="Song or URL",
        placeholder="e.g. drake or https://soundcloud.com/...",
        max_length=200,
        required=True,
    )

    def __init__(self, state: MusicPanelState, *, mode: str, modal_title: str):
        super().__init__(title=modal_title)
        self.state = state
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction) -> None:
        q = self.query.value.strip()
        if not q:
            await interaction.response.send_message("Enter a search term or URL.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_queue(member, voice_channel_id=_voice_id(self.state.session)):
                raise UserFacingError("You cannot queue music right now.")
            channel = require_voice(member)
            if not channel:
                raise UserFacingError("Join a voice channel first.")
            await self.state.session.ensure_player(channel)

            if self.mode == "play":
                msg = await self.state.session.play_query(member, q)
                await refresh_panel_message(self.state.session, self.state.bot, notice=msg)
            else:
                tracks = await search_tracks(q, limit=10)
                if not tracks:
                    raise UserFacingError("No results found.")

                async def on_added() -> None:
                    await refresh_panel_message(
                        self.state.session,
                        self.state.bot,
                        notice="Track added to queue.",
                    )

                view = MusicSearchView(
                    self.state.session,
                    tracks,
                    interaction.user.id,
                    self.state.bot,
                    on_track_added=on_added,
                )
                embed = discord.Embed(
                    title="Search results",
                    description=f"Results for **{q}**",
                    color=discord.Color.from_str(ConfigManager.get("EMBED_COLOR")),
                )
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                await refresh_panel_message(
                    self.state.session,
                    self.state.bot,
                    notice="Pick a track from the search menu above.",
                )
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
        except Exception:
            await interaction.followup.send(
                "Something went wrong. Try again or use the web dashboard.",
                ephemeral=True,
            )


class _MPPlayButton(discord.ui.Button):
    def __init__(self, state: MusicPanelState):
        super().__init__(
            label="Play / queue",
            style=discord.ButtonStyle.success,
            emoji="▶️",
            custom_id="mp_play",
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.send_modal(
            _QueryModal(self._state, mode="play", modal_title="Play music")
        )


class _MPSearchButton(discord.ui.Button):
    def __init__(self, state: MusicPanelState):
        super().__init__(
            label="Search",
            style=discord.ButtonStyle.primary,
            emoji="🔎",
            custom_id="mp_search",
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.send_modal(
            _QueryModal(self._state, mode="search", modal_title="Search music")
        )


class _MPJoinButton(discord.ui.Button):
    def __init__(self, state: MusicPanelState):
        super().__init__(
            label="Join VC",
            style=discord.ButtonStyle.secondary,
            emoji="🔊",
            custom_id="mp_join",
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            channel = require_voice(member)
            if not channel:
                raise UserFacingError("Join a voice channel first.")
            await self._state.session.ensure_player(channel)
            state = MusicPanelState(
                session=self._state.session,
                bot=self._state.bot,
                owner_id=self._state.owner_id,
                panel_url=self._state.panel_url,
                guild=self._state.guild,
                notice=f"Joined **{channel.name}**.",
            )
            await edit_music_panel(interaction, state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPRefreshButton(discord.ui.Button):
    def __init__(self, state: MusicPanelState):
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
            custom_id="mp_refresh",
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        state = MusicPanelState(
            session=self._state.session,
            bot=self._state.bot,
            owner_id=self._state.owner_id,
            panel_url=self._state.session.public_url(),
            guild=self._state.guild,
            notice="Panel updated.",
        )
        await edit_music_panel(interaction, state)


class _MPPauseButton(discord.ui.Button):
    def __init__(self, state: MusicPanelState, *, paused: bool, disabled: bool):
        super().__init__(
            label="Resume" if paused else "Pause",
            style=discord.ButtonStyle.primary,
            emoji="▶️" if paused else "⏸️",
            custom_id="mp_pause",
            disabled=disabled,
        )
        self._state = state
        self._action = "resume" if paused else "pause"

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await _panel_act(interaction, self._state, self._action)


class _MPActionButton(discord.ui.Button):
    def __init__(
        self,
        state: MusicPanelState,
        *,
        label: str,
        emoji: str,
        action: str,
        style: discord.ButtonStyle,
        custom_id: str,
    ):
        super().__init__(label=label, emoji=emoji, style=style, custom_id=custom_id)
        self._state = state
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await _panel_act(interaction, self._state, self._action)


async def _panel_act(interaction: discord.Interaction, state: MusicPanelState, action: str) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        member = interaction.guild.get_member(interaction.user.id)
        if not can_control(member, voice_channel_id=_voice_id(state.session)):
            raise UserFacingError("You cannot control playback.")
        msg = await getattr(state.session, action)()
        new_state = MusicPanelState(
            session=state.session,
            bot=state.bot,
            owner_id=state.owner_id,
            panel_url=state.panel_url,
            guild=state.guild,
            notice=msg,
        )
        await edit_music_panel(interaction, new_state)
    except UserFacingError as exc:
        await interaction.followup.send(exc.user_message, ephemeral=True)
    except Exception:
        await interaction.followup.send("Action failed.", ephemeral=True)


class _MPLoopSelect(discord.ui.Select):
    def __init__(self, state: MusicPanelState):
        session_state = state.session.state_dict()
        loop = session_state.get("loopMode", "off")
        super().__init__(
            placeholder=f"Loop: {loop}",
            custom_id="mp_loop",
            options=[
                discord.SelectOption(label="Loop off", value="off", default=loop == "off"),
                discord.SelectOption(label="Loop track", value="track", default=loop == "track"),
                discord.SelectOption(label="Loop queue", value="queue", default=loop == "queue"),
            ],
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_control(member, voice_channel_id=_voice_id(self._state.session)):
                raise UserFacingError("You cannot control playback.")
            mode = LoopMode(interaction.data.get("values")[0])
            msg = await self._state.session.set_loop(mode)
            new_state = MusicPanelState(
                session=self._state.session,
                bot=self._state.bot,
                owner_id=self._state.owner_id,
                panel_url=self._state.panel_url,
                guild=self._state.guild,
                notice=msg,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPRemoveSelect(discord.ui.Select):
    def __init__(self, state: MusicPanelState, queue: list):
        super().__init__(
            placeholder="Remove from queue…",
            custom_id="mp_remove",
            options=[
                discord.SelectOption(
                    label=(t.get("title") or "Unknown")[:100],
                    description=(t.get("author") or "")[:100],
                    value=str(i),
                )
                for i, t in enumerate(queue[:25])
            ],
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_control(member, voice_channel_id=_voice_id(self._state.session)):
                raise UserFacingError("You cannot control playback.")
            idx = int(interaction.data.get("values")[0])
            msg = await self._state.session.remove_at(idx)
            new_state = MusicPanelState(
                session=self._state.session,
                bot=self._state.bot,
                owner_id=self._state.owner_id,
                panel_url=self._state.panel_url,
                guild=self._state.guild,
                notice=msg,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPVolumeSelect(discord.ui.Select):
    def __init__(self, state: MusicPanelState, volume: int):
        super().__init__(
            placeholder=f"Volume ({volume}%)",
            custom_id="mp_volume",
            options=[
                discord.SelectOption(label="25%", value="25"),
                discord.SelectOption(label="50%", value="50"),
                discord.SelectOption(label="75%", value="75"),
                discord.SelectOption(label="100%", value="100", default=volume == 100),
                discord.SelectOption(label="125%", value="125"),
            ],
        )
        self._state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await check_panel_owner(interaction, self._state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_control(member, voice_channel_id=_voice_id(self._state.session)):
                raise UserFacingError("You cannot control playback.")
            level = int(interaction.data.get("values")[0])
            msg = await self._state.session.set_volume(level)
            new_state = MusicPanelState(
                session=self._state.session,
                bot=self._state.bot,
                owner_id=self._state.owner_id,
                panel_url=self._state.panel_url,
                guild=self._state.guild,
                notice=msg,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class MusicPanelLayoutView(discord.ui.LayoutView):
    def __init__(
        self,
        interaction: discord.Interaction | None,
        state: MusicPanelState,
    ) -> None:
        super().__init__(timeout=900)
        self.state = state

        title_md, status_md, queue_md, dashboard_md, artwork_url = build_panel_markdown(state)
        session_state = state.session.state_dict()
        player = state.session.get_player()
        paused = bool(player and player.paused)
        playing = bool(player and player.playing)
        queue = session_state.get("queue") or []

        inner: list = []
        thumb_media: str | None = None
        if artwork_url and artwork_url.startswith(("http://", "https://")):
            thumb_media = artwork_url

        if thumb_media:
            inner.append(
                discord.ui.Section(
                    discord.ui.TextDisplay(title_md),
                    accessory=discord.ui.Thumbnail(thumb_media, description="Now playing"),
                )
            )
        else:
            inner.append(discord.ui.TextDisplay(title_md))

        inner.append(discord.ui.TextDisplay(status_md))
        inner.append(discord.ui.Separator(visible=True, spacing=SeparatorSpacing.large))
        inner.append(discord.ui.TextDisplay(queue_md))
        inner.append(discord.ui.Separator(visible=True, spacing=SeparatorSpacing.small))
        inner.append(discord.ui.TextDisplay(dashboard_md))

        row1 = discord.ui.ActionRow(
            _MPPlayButton(state),
            _MPSearchButton(state),
            _MPJoinButton(state),
            _MPRefreshButton(state),
        )
        row2 = discord.ui.ActionRow(
            _MPPauseButton(state, paused=paused, disabled=not playing and not paused),
            _MPActionButton(
                state,
                label="Skip",
                emoji="⏭️",
                action="skip",
                style=discord.ButtonStyle.secondary,
                custom_id="mp_skip",
            ),
            _MPActionButton(
                state,
                label="Stop",
                emoji="⏹️",
                action="stop",
                style=discord.ButtonStyle.danger,
                custom_id="mp_stop",
            ),
            _MPActionButton(
                state,
                label="Shuffle",
                emoji="🔀",
                action="shuffle_queue",
                style=discord.ButtonStyle.secondary,
                custom_id="mp_shuffle",
            ),
        )
        row3 = discord.ui.ActionRow(_MPLoopSelect(state))

        if queue:
            row4 = discord.ui.ActionRow(_MPRemoveSelect(state, queue))
        else:
            row4 = discord.ui.ActionRow(
                _MPVolumeSelect(state, session_state.get("volume", 100))
            )

        row5 = discord.ui.ActionRow(
            discord.ui.Button(
                label="Open web dashboard",
                style=discord.ButtonStyle.link,
                url=state.panel_url,
            )
        )

        inner.extend([row1, row2, row3, row4, row5])
        inner.append(discord.ui.TextDisplay(ConfigManager.get("FOOTER") or ""))

        self.add_item(discord.ui.Container(*inner, accent_color=_accent_int()))

    async def on_timeout(self) -> None:
        self.state.session.clear_panel_binding()
        await super().on_timeout()
