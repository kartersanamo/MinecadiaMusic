"""Components V2 music player panel."""

from __future__ import annotations

import logging

import discord
from discord.enums import SeparatorSpacing
from discord.ext import commands

from core.action_log import log_interaction
from core.config import ConfigManager
from core.errors.exceptions import UserFacingError
from core.loggers import log_ui
from services.music.permissions import can_control, can_queue, require_voice
from services.music.queue import LoopMode
from services.music.resolver import search_media
from ui.views.music_panel_support import (
    MusicPanelState,
    QUEUE_PAGE_SIZE,
    build_panel_markdown,
    check_panel_owner,
    edit_music_panel,
    refresh_panel_message,
    resolve_panel_state,
    _accent_int,
)
from core.activity_entry_point import launch_music_activity
from ui.views.music_search_view import MusicSearchView, search_results_embed

log = logging.getLogger("UI")


def _voice_id(session) -> int | None:
    player = session.get_player()
    if player and player.channel:
        return player.channel.id
    return None


class _QueryModal(discord.ui.Modal, title="Play music"):
    query = discord.ui.TextInput(
        label="Song or URL",
        placeholder="e.g. song name or https://youtube.com/...",
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
                await self.state.session.play_query(member, q)
                await refresh_panel_message(self.state.session, self.state.bot)
            else:
                results = await search_media(q, limit=10)
                if not results:
                    raise UserFacingError("No results found.")

                async def on_added() -> None:
                    await refresh_panel_message(self.state.session, self.state.bot)

                view = MusicSearchView(
                    self.state.session,
                    results,
                    interaction.user.id,
                    self.state.bot,
                    on_track_added=on_added,
                )
                embed = search_results_embed(results, q)
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                try:
                    await refresh_panel_message(
                        self.state.session,
                        self.state.bot,
                        notice="Pick a track from the search menu above.",
                    )
                except Exception:
                    pass
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
        except Exception:
            log.exception("Search/play modal failed")
            await interaction.followup.send(
                "Something went wrong. Try again or use the web dashboard.",
                ephemeral=True,
            )


class _MPPlayButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(
            label="Play / queue",
            style=discord.ButtonStyle.success,
            custom_id="mp_play",
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.send_modal(
            _QueryModal(state, mode="play", modal_title="Play music")
        )


class _MPSearchButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(
            label="Search",
            style=discord.ButtonStyle.primary,
            custom_id="mp_search",
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.send_modal(
            _QueryModal(state, mode="search", modal_title="Search music")
        )


class _MPJoinButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(
            label="Join VC",
            style=discord.ButtonStyle.secondary,
            custom_id="mp_join",
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            channel = require_voice(member)
            if not channel:
                raise UserFacingError("Join a voice channel first.")
            await state.session.ensure_player(channel)
            state = MusicPanelState(
                session=state.session,
                bot=state.bot,
                owner_id=state.owner_id,
                panel_url=state.panel_url,
                guild=state.guild,
                notice=f"Joined **{channel.name}**.",
            )
            await edit_music_panel(interaction, state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPRefreshButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            custom_id="mp_refresh",
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        state = MusicPanelState(
            session=state.session,
            bot=state.bot,
            owner_id=state.owner_id,
            panel_url=state.session.public_url(),
            guild=state.guild,
            notice="Panel updated.",
        )
        await edit_music_panel(interaction, state)


class _MPPauseButton(discord.ui.Button):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        paused: bool = False,
        disabled: bool = False,
    ):
        super().__init__(
            label="Resume" if paused else "Pause",
            style=discord.ButtonStyle.primary,
            custom_id="mp_pause",
            disabled=disabled,
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        player = state.session.get_player()
        action = "resume" if player and player.paused else "pause"
        await _panel_act(interaction, state, action)


class _MPActionButton(discord.ui.Button):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        label: str,
        action: str,
        style: discord.ButtonStyle,
        custom_id: str,
        disabled: bool = False,
    ):
        super().__init__(label=label, style=style, custom_id=custom_id, disabled=disabled)
        self._bot = bot
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await _panel_act(interaction, state, self._action)


async def _panel_act(interaction: discord.Interaction, state: MusicPanelState, action: str) -> None:
    log_interaction(log_ui, interaction, f"panel.{action}")
    await interaction.response.defer(ephemeral=True)
    try:
        member = interaction.guild.get_member(interaction.user.id)
        if not can_control(member, voice_channel_id=_voice_id(state.session)):
            raise UserFacingError("You cannot control playback.")
        actor_id = interaction.user.id
        result = await getattr(state.session, action)(actor_id=actor_id)
        log_ui.info(
            "panel.%s ok guild_id=%s user_id=%s result=%s",
            action,
            interaction.guild.id,
            actor_id,
            str(result)[:120],
        )
        new_state = MusicPanelState(
            session=state.session,
            bot=state.bot,
            owner_id=state.owner_id,
            panel_url=state.panel_url,
            guild=state.guild,
        )
        await edit_music_panel(interaction, new_state)
    except UserFacingError as exc:
        log_ui.info(
            "panel.%s rejected guild_id=%s user_id=%s reason=%s",
            action,
            interaction.guild.id if interaction.guild else None,
            interaction.user.id,
            exc.user_message,
        )
        await interaction.followup.send(exc.user_message, ephemeral=True)
    except Exception:
        log_ui.exception(
            "panel.%s failed guild_id=%s user_id=%s",
            action,
            interaction.guild.id if interaction.guild else None,
            interaction.user.id,
        )
        await interaction.followup.send("Action failed.", ephemeral=True)


class _MPLoopSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, *, loop: str = "off"):
        super().__init__(
            placeholder=f"Loop: {loop}",
            custom_id="mp_loop",
            options=[
                discord.SelectOption(label="Loop off", value="off", default=loop == "off"),
                discord.SelectOption(label="Loop track", value="track", default=loop == "track"),
                discord.SelectOption(label="Loop queue", value="queue", default=loop == "queue"),
            ],
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_control(member, voice_channel_id=_voice_id(state.session)):
                raise UserFacingError("You cannot control playback.")
            mode = LoopMode(interaction.data.get("values")[0])
            await state.session.set_loop(mode, actor_id=interaction.user.id)
            new_state = MusicPanelState(
                session=state.session,
                bot=state.bot,
                owner_id=state.owner_id,
                panel_url=state.panel_url,
                guild=state.guild,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPLaunchActivityButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, *, disabled: bool = False):
        super().__init__(
            label="Launch Dashboard",
            style=discord.ButtonStyle.primary,
            custom_id="mp_launch_activity",
            disabled=disabled,
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await launch_music_activity(interaction, panel_url=state.panel_url)


class _MPRemoveSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, queue: list | None = None):
        items = queue or []
        if items:
            option_count = min(len(items), 25)
            super().__init__(
                placeholder="Remove from queue (select multiple)…",
                custom_id="mp_remove",
                min_values=1,
                max_values=option_count,
                options=[
                    discord.SelectOption(
                        label=(t.get("title") or "Unknown")[:100],
                        description=(
                            f"{(t.get('author') or '')[:60]}"
                            + (
                                f" · {t.get('requesterName')}"
                                if t.get("requesterName")
                                else ""
                            )
                        )[:100],
                        value=str(i),
                    )
                    for i, t in enumerate(items[:25])
                ],
            )
        else:
            super().__init__(
                placeholder="Remove from queue…",
                custom_id="mp_remove",
                options=[
                    discord.SelectOption(
                        label="—",
                        description="Nothing queued",
                        value="0",
                    )
                ],
                disabled=True,
            )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_control(member, voice_channel_id=_voice_id(state.session)):
                raise UserFacingError("You cannot control playback.")
            values = interaction.data.get("values") or []
            indices = [int(v) for v in values]
            if len(indices) == 1:
                await state.session.remove_at(indices[0], actor_id=interaction.user.id)
            else:
                await state.session.remove_many(indices, actor_id=interaction.user.id)
            new_state = MusicPanelState(
                session=state.session,
                bot=state.bot,
                owner_id=state.owner_id,
                panel_url=state.panel_url,
                guild=state.guild,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPQueuePageButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, *, direction: int, disabled: bool = False):
        super().__init__(
            label="◀ Prev" if direction < 0 else "Next ▶",
            style=discord.ButtonStyle.secondary,
            custom_id="mp_queue_prev" if direction < 0 else "mp_queue_next",
            disabled=disabled,
        )
        self._bot = bot
        self._direction = direction

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            state.session.bump_queue_page(self._direction, QUEUE_PAGE_SIZE)
            new_state = MusicPanelState(
                session=state.session,
                bot=state.bot,
                owner_id=state.owner_id,
                panel_url=state.panel_url,
                guild=state.guild,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


class _MPVolumeSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, volume: int = 100):
        super().__init__(
            placeholder=f"Volume ({volume}%)",
            custom_id="mp_volume",
            options=[
                discord.SelectOption(label="25%", value="25", default=volume == 25),
                discord.SelectOption(label="50%", value="50", default=volume == 50),
                discord.SelectOption(label="75%", value="75", default=volume == 75),
                discord.SelectOption(label="100%", value="100", default=volume == 100),
                discord.SelectOption(label="125%", value="125", default=volume == 125),
            ],
        )
        self._bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        state = await resolve_panel_state(interaction, self._bot)
        if not state or not await check_panel_owner(interaction, state.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not can_control(member, voice_channel_id=_voice_id(state.session)):
                raise UserFacingError("You cannot control playback.")
            level = int(interaction.data.get("values")[0])
            await state.session.set_volume(level, actor_id=interaction.user.id)
            new_state = MusicPanelState(
                session=state.session,
                bot=state.bot,
                owner_id=state.owner_id,
                panel_url=state.panel_url,
                guild=state.guild,
            )
            await edit_music_panel(interaction, new_state)
        except UserFacingError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)


def _dashboard_row(
    bot: commands.Bot,
    panel_url: str,
    *,
    register_only: bool = False,
) -> discord.ui.ActionRow:
    return discord.ui.ActionRow(
        _MPLaunchActivityButton(bot, disabled=register_only),
        discord.ui.Button(
            label="Open in browser",
            style=discord.ButtonStyle.link,
            url=panel_url,
        ),
    )


def _build_panel_rows(
    bot: commands.Bot,
    state: MusicPanelState | None,
    *,
    register_only: bool = False,
) -> tuple[list, discord.ui.ActionRow | None]:
    if register_only or state is None:
        bot_ref = bot
        session_state = {"queue": [], "volume": 100, "loopMode": "off"}
        paused = False
        playing = False
        queue: list = []
        panel_url = "https://discord.com"
    else:
        bot_ref = state.bot
        session_state = state.session.state_dict()
        player = state.session.get_player()
        paused = bool(player and player.paused)
        playing = bool(player and player.playing)
        queue = session_state.get("queue") or []
        panel_url = state.panel_url

    row1 = discord.ui.ActionRow(
        _MPPlayButton(bot_ref),
        _MPSearchButton(bot_ref),
        _MPJoinButton(bot_ref),
        _MPRefreshButton(bot_ref),
    )
    row2 = discord.ui.ActionRow(
        _MPPauseButton(bot_ref, paused=paused, disabled=not playing and not paused),
        _MPActionButton(
            bot_ref,
            label="Skip",
            action="skip",
            style=discord.ButtonStyle.secondary,
            custom_id="mp_skip",
        ),
        _MPActionButton(
            bot_ref,
            label="Stop",
            action="stop",
            style=discord.ButtonStyle.danger,
            custom_id="mp_stop",
        ),
        _MPActionButton(
            bot_ref,
            label="Shuffle",
            action="shuffle_queue",
            style=discord.ButtonStyle.secondary,
            custom_id="mp_shuffle",
        ),
        _MPActionButton(
            bot_ref,
            label="Clear",
            action="clear_queue",
            style=discord.ButtonStyle.danger,
            custom_id="mp_clear",
            disabled=register_only or not queue,
        ),
    )
    row3 = discord.ui.ActionRow(
        _MPLoopSelect(bot_ref, loop=session_state.get("loopMode", "off"))
    )
    volume_row = discord.ui.ActionRow(
        _MPVolumeSelect(bot_ref, session_state.get("volume", 100))
    )

    queue_nav_row = None
    if not register_only and state is not None and len(queue) > QUEUE_PAGE_SIZE:
        state.session.clamp_queue_page(QUEUE_PAGE_SIZE)
        page = state.session.queue_page
        max_page = max(0, (len(queue) - 1) // QUEUE_PAGE_SIZE)
        queue_nav_row = discord.ui.ActionRow(
            _MPQueuePageButton(bot_ref, direction=-1, disabled=page <= 0),
            _MPQueuePageButton(bot_ref, direction=1, disabled=page >= max_page),
        )

    if register_only:
        row4 = discord.ui.ActionRow(_MPRemoveSelect(bot_ref))
        rows = [row1, row2, row3, row4, volume_row]
        row5 = _dashboard_row(bot_ref, panel_url, register_only=True)
    elif queue:
        rows = [row1, row2, row3]
        if queue_nav_row is not None:
            rows.append(queue_nav_row)
        rows.extend(
            [
                discord.ui.ActionRow(_MPRemoveSelect(bot_ref, queue)),
                volume_row,
            ]
        )
        row5 = _dashboard_row(bot_ref, panel_url)
    else:
        rows = [row1, row2, row3, volume_row]
        row5 = _dashboard_row(bot_ref, panel_url)

    return rows, row5


class MusicPanelLayoutView(discord.ui.LayoutView):
    def __init__(
        self,
        interaction: discord.Interaction | None,
        state: MusicPanelState | None = None,
        *,
        bot: commands.Bot | None = None,
        register_only: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.state = state

        if register_only:
            if bot is None:
                raise ValueError("bot is required when register_only=True")
            title_md = "# Music Player"
            status_md = "*Controls remain active after bot restarts.*"
            queue_md = "**Queue**"
            activity_md = "**Activity**"
            dashboard_md = "*Use `/music` to refresh a stale panel.*"
            rows, row5 = _build_panel_rows(bot, None, register_only=True)
        else:
            if state is None:
                raise ValueError("state is required when register_only=False")
            title_md, status_md, queue_md, activity_md, dashboard_md, _artwork_url = (
                build_panel_markdown(state)
            )
            rows, row5 = _build_panel_rows(state.bot, state)

        inner: list = [discord.ui.TextDisplay(title_md)]
        inner.append(discord.ui.TextDisplay(status_md))
        inner.append(discord.ui.Separator(visible=True, spacing=SeparatorSpacing.large))
        inner.append(discord.ui.TextDisplay(queue_md))
        inner.append(discord.ui.Separator(visible=True, spacing=SeparatorSpacing.small))
        inner.append(discord.ui.TextDisplay(activity_md))
        inner.append(discord.ui.Separator(visible=True, spacing=SeparatorSpacing.small))
        inner.append(discord.ui.TextDisplay(dashboard_md))

        inner.extend(rows)
        if row5 is not None:
            inner.append(row5)
        inner.append(discord.ui.TextDisplay(ConfigManager.get("FOOTER") or ""))

        self.add_item(discord.ui.Container(*inner, accent_color=_accent_int()))


def register_persistent_music_panel(bot: commands.Bot) -> None:
    bot.add_view(MusicPanelLayoutView(None, bot=bot, register_only=True))
