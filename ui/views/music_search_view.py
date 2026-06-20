from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from core.action_log import log_action
from core.config import ConfigManager
from core.loggers import log_ui
from services.music.resolver import tracks_from_identifier
from services.music.search_results import SearchResult, _format_ms, external_url, markdown_link


def _embed_color() -> discord.Color:
    return discord.Color.from_str(ConfigManager.get("EMBED_COLOR"))


def _link_button(url: str | None, *, label: str = "Open source") -> discord.ui.Button | None:
    if not url:
        return None
    return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url)


def search_results_embed(
    results: list[SearchResult],
    query: str,
    *,
    page: int = 0,
    total_pages: int = 1,
    total: int | None = None,
) -> discord.Embed:
    lines: list[str] = []
    for i, item in enumerate(results, 1):
        link = markdown_link(item.title, item.uri, item.identifier)
        if item.kind == "playlist":
            lines.append(f"`{i}.` {link} — *{item.track_count} tracks* · {item.author}")
        else:
            dur = _format_ms(item.duration_ms)
            lines.append(f"`{i}.` {link} — {item.author} · `{dur}`")

    description = "\n".join(lines) if lines else "*No results*"
    description += "\n\n*Pick a result below, or click a title above to open it.*"

    embed = discord.Embed(
        title="Search results",
        description=description,
        color=_embed_color(),
    )
    footer = f"Query: {query[:60]}"
    if total_pages > 1:
        count = total if total is not None else len(results)
        footer += f" · Page {page + 1}/{total_pages} · {count} results"
    embed.set_footer(text=footer)
    first_url = external_url(results[0].uri, results[0].identifier) if results else None
    if first_url:
        embed.url = first_url
    return embed


def _playlist_embed(item: SearchResult) -> discord.Embed:
    lines = []
    for i, t in enumerate(item.tracks, 1):
        link = markdown_link(t.title, t.uri, t.identifier)
        dur = f" · `{_format_ms(t.duration_ms)}`" if t.duration_ms else ""
        lines.append(f"`{i}.` {link} — {t.author}{dur}")
        if len("\n".join(lines)) > 3800:
            remaining = item.track_count - i
            if remaining > 0:
                lines.append(f"*+ {remaining} more tracks — use **Open playlist** below*")
            break
    embed = discord.Embed(
        title=f"Playlist: {item.title}",
        description="\n".join(lines) if lines else "*Empty playlist*",
        color=_embed_color(),
    )
    embed.set_footer(text=f"{item.track_count} tracks · {item.author}")
    playlist_url = external_url(item.uri, item.identifier)
    if playlist_url:
        embed.url = playlist_url
    if item.artwork:
        embed.set_thumbnail(url=item.artwork)
    return embed


def _track_added_embed(track, *, started: bool) -> discord.Embed:
    link = markdown_link(track.title, track.uri, track.identifier)
    embed = discord.Embed(
        description=f"{'Now playing' if started else 'Queued'} {link}",
        color=_embed_color(),
    )
    track_url = external_url(track.uri, track.identifier)
    if track_url:
        embed.url = track_url
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


def _track_added_view(track) -> discord.ui.View | None:
    url = external_url(track.uri, track.identifier)
    button = _link_button(url, label="Open video")
    if not button:
        return None
    view = discord.ui.View()
    view.add_item(button)
    return view


class MusicPlaylistConfirmView(discord.ui.View):
    def __init__(
        self,
        session,
        item: SearchResult,
        requester_id: int,
        bot,
        *,
        on_added: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__(timeout=120)
        self.session = session
        self.item = item
        self.requester_id = requester_id
        self.bot = bot
        self.on_added = on_added
        playlist_url = external_url(item.uri, item.identifier)
        link_btn = _link_button(playlist_url, label="Open playlist")
        if link_btn:
            self.add_item(link_btn)

    @discord.ui.button(
        label="Add all tracks",
        style=discord.ButtonStyle.success,
        custom_id="mp_pl_add_all",
    )
    async def add_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            log_action(
                log_ui,
                "search.playlist_add",
                user_id=interaction.user.id,
                guild_id=interaction.guild.id if interaction.guild else None,
                playlist=self.item.title,
                track_count=self.item.track_count,
            )
            tracks = await tracks_from_identifier(self.item.identifier, kind="playlist")
            member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
            count, started = await self.session.add_tracks(
                tracks,
                self.requester_id,
                connect_member=member,
                playlist_title=self.item.title,
            )
            noun = "Now playing" if started else "Queued"
            link = markdown_link(self.item.title, self.item.uri, self.item.identifier)
            await interaction.followup.send(
                f"{noun} playlist {link} ({count} tracks).",
                ephemeral=True,
            )
            if self.on_added:
                await self.on_added()
            self.stop()
        except Exception as exc:
            from core.errors.exceptions import UserFacingError

            msg = exc.user_message if isinstance(exc, UserFacingError) else "Could not add playlist."
            await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        custom_id="mp_pl_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Playlist not added.", ephemeral=True)
        self.stop()


class _SearchPageButton(discord.ui.Button):
    def __init__(self, direction: int, *, disabled: bool = False):
        super().__init__(
            label="◀ Prev" if direction < 0 else "Next ▶",
            style=discord.ButtonStyle.secondary,
            custom_id="mp_search_prev" if direction < 0 else "mp_search_next",
            disabled=disabled,
            row=1,
        )
        self._direction = direction

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, MusicSearchView):
            return
        await view.change_page(interaction, view.page + self._direction)


class MusicSearchView(discord.ui.View):
    def __init__(
        self,
        session,
        query: str,
        page_data: dict,
        requester_id: int,
        bot,
        *,
        on_track_added: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__(timeout=120)
        self.session = session
        self.query = query
        self.page = int(page_data.get("page", 0))
        self.total_pages = int(page_data.get("totalPages", 1))
        self.requester_id = requester_id
        self.bot = bot
        self.on_track_added = on_track_added
        self._results: list[SearchResult] = list(page_data.get("results") or [])

        options = []
        for i, item in enumerate(self._results[:25]):
            if item.kind == "playlist":
                label = f"Playlist: {item.title}"[:100]
                desc = f"{item.track_count} tracks · {item.author}"[:100]
            else:
                label = (item.title or "Unknown")[:100]
                desc = (item.author or "")[:100]
            options.append(
                discord.SelectOption(label=label, description=desc, value=str(i))
            )
        select = discord.ui.Select(
            placeholder="Choose a track or playlist…",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

        if self.total_pages > 1:
            self.add_item(_SearchPageButton(-1, disabled=self.page <= 0))
            self.add_item(_SearchPageButton(1, disabled=self.page >= self.total_pages - 1))

    async def change_page(self, interaction: discord.Interaction, new_page: int) -> None:
        await interaction.response.defer()
        data = await self.session.get_search_page(self.query, page=new_page)
        if not data["results"]:
            await interaction.followup.send("No more results on that page.", ephemeral=True)
            return
        view = MusicSearchView(
            self.session,
            self.query,
            data,
            self.requester_id,
            self.bot,
            on_track_added=self.on_track_added,
        )
        embed = search_results_embed(
            data["results"],
            self.query,
            page=data["page"],
            total_pages=data["totalPages"],
            total=data["total"],
        )
        await interaction.edit_original_response(embed=embed, view=view)
        self.stop()

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            idx = int(interaction.data.get("values")[0])
            item = self._results[idx]

            if item.kind == "playlist":
                view = MusicPlaylistConfirmView(
                    self.session,
                    item,
                    self.requester_id,
                    self.bot,
                    on_added=self.on_track_added,
                )
                for child in view.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == "mp_pl_add_all":
                        child.label = f"Add all {item.track_count} tracks"
                        break
                await interaction.followup.send(
                    embed=_playlist_embed(item),
                    view=view,
                    ephemeral=True,
                )
                self.stop()
                return

            tracks = await tracks_from_identifier(item.identifier, kind="track")
            track = tracks[0]
            track.extras = {"requester_id": self.requester_id}
            log_action(
                log_ui,
                "search.track_add",
                user_id=interaction.user.id,
                guild_id=interaction.guild.id if interaction.guild else None,
                track=track.title,
            )
            member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
            started = await self.session.add_tracks(
                [track],
                self.requester_id,
                connect_member=member,
            )
            embed = _track_added_embed(track, started=started[1])
            view = _track_added_view(track)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            if self.on_track_added:
                await self.on_track_added()
            self.stop()
        except Exception as exc:
            from core.errors.exceptions import UserFacingError

            msg = exc.user_message if isinstance(exc, UserFacingError) else "Could not add that track."
            await interaction.followup.send(msg, ephemeral=True)
