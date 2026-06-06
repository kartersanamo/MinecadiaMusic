"""Shared state and edit helpers for the Components V2 music panel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from core.config import ConfigManager
from services.music.search_results import markdown_link

if TYPE_CHECKING:
    from services.music.session_manager import GuildMusicSession


@dataclass
class MusicPanelState:
    session: "GuildMusicSession"
    bot: commands.Bot
    owner_id: int
    panel_url: str
    guild: discord.Guild
    notice: str | None = None


def _format_ms(ms: int) -> str:
    if ms <= 0:
        return "0:00"
    seconds = ms // 1000
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _accent_int() -> int:
    return discord.Color.from_str(ConfigManager.get("EMBED_COLOR")).value


def _requester_label(guild: discord.Guild, track: dict) -> str:
    rid = track.get("requesterId")
    if not rid:
        return ""
    return f" · <@{rid}>"


def build_panel_markdown(state: MusicPanelState) -> tuple[str, str, str, str, str, str | None]:
    """Returns title_md, status_md, queue_md, activity_md, dashboard_md, artwork_url."""
    session_state = state.session.state_dict()
    lines: list[str] = []

    if state.notice:
        lines.append(f"*{state.notice}*")
        lines.append("")

    player = state.session.get_player()
    if player and player.channel:
        lines.append(f"**Voice:** {player.channel.mention}")
    else:
        lines.append("**Voice:** Not connected")
    lines.append(f"**Loop:** {session_state.get('loopMode', 'off').upper()}")
    lines.append(f"**Volume:** {session_state.get('volume', 100)}%")

    title_md = "# Music Player"
    artwork_url: str | None = None

    if session_state.get("current"):
        c = session_state["current"]
        pos = _format_ms(session_state.get("positionMs") or 0)
        title_md = f"# {markdown_link(c['title'], c.get('uri'), c.get('identifier'), bold=True)}"
        lines.append("")
        requester = _requester_label(state.guild, c)
        lines.append(f"by **{c['author']}** · `{pos}` / `{c['durationText']}`{requester}")
        if session_state.get("paused"):
            lines.append("*Paused*")
        elif session_state.get("playing"):
            lines.append("*Playing*")
        artwork_url = c.get("artwork")
    else:
        lines.append("")
        lines.append("*Nothing playing — use **Play / queue** or **Search** below.*")

    status_md = "\n".join(lines)

    queue = session_state.get("queue") or []
    queue_lines = ["**Queue**"]
    if queue:
        for i, t in enumerate(queue[:8], 1):
            title = markdown_link(
                (t.get("title") or "Unknown")[:60],
                t.get("uri"),
                t.get("identifier"),
            )
            author = (t.get("author") or "")[:40]
            requester = _requester_label(state.guild, t)
            queue_lines.append(f"`{i}.` {title} — {author}{requester}")
        if len(queue) > 8:
            queue_lines.append(f"*+ {len(queue) - 8} more in queue*")
    else:
        queue_lines.append("*Empty*")
    queue_md = "\n".join(queue_lines)

    activity = session_state.get("activity") or []
    activity_lines = ["**Activity**"]
    if activity:
        for entry in activity[-6:]:
            actor_id = entry.get("actorId")
            mention = f"<@{actor_id}>" if actor_id else entry.get("actorName", "Someone")
            at = entry.get("at")
            time_md = f"<t:{int(float(at))}:R> " if at else ""
            activity_lines.append(f"• {time_md}{mention} {entry.get('text', '')}")
    else:
        activity_lines.append("*No recent actions*")
    activity_md = "\n".join(activity_lines)

    dashboard_md = (
        f"[Open full panel in browser]({state.panel_url})\n"
    )

    return title_md, status_md, queue_md, activity_md, dashboard_md, artwork_url


async def check_panel_owner(interaction: discord.Interaction, owner_id: int) -> bool:
    if interaction.user.id == owner_id:
        return True
    await interaction.response.send_message(
        "This panel belongs to someone else. Run **`/music`** for your own.",
        ephemeral=True,
    )
    return False


def panel_state_from_interaction(
    interaction: discord.Interaction,
    bot: commands.Bot,
) -> MusicPanelState:
    from core.errors.exceptions import UserFacingError

    if not interaction.guild:
        raise UserFacingError("This can only be used in a server.")
    session = bot.app.music.sessions.get(interaction.guild.id)
    if not session or not session.panel_owner_id:
        raise UserFacingError("No active music panel. Run **`/music`** to open one.")
    return MusicPanelState(
        session=session,
        bot=bot,
        owner_id=session.panel_owner_id,
        panel_url=session.public_url(),
        guild=interaction.guild,
    )


async def resolve_panel_state(
    interaction: discord.Interaction,
    bot: commands.Bot,
) -> MusicPanelState | None:
    from core.errors.exceptions import UserFacingError

    try:
        return panel_state_from_interaction(interaction, bot)
    except UserFacingError as exc:
        if interaction.response.is_done():
            await interaction.followup.send(exc.user_message, ephemeral=True)
        else:
            await interaction.response.send_message(exc.user_message, ephemeral=True)
        return None


async def edit_music_panel(interaction: discord.Interaction, state: MusicPanelState) -> None:
    from ui.views.music_panel_layout_view import MusicPanelLayoutView

    if interaction.message is not None:
        state.session.bind_panel_message(interaction.message, state.owner_id)

    view = MusicPanelLayoutView(interaction, state)
    kwargs: dict[str, Any] = {"content": None, "embed": None, "view": view}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    else:
        await interaction.response.edit_message(**kwargs)


async def refresh_panel_message(
    session: "GuildMusicSession",
    bot: commands.Bot,
    *,
    notice: str | None = None,
) -> None:
    msg = session.panel_message
    owner_id = session.panel_owner_id
    if not msg or not owner_id:
        return
    guild = bot.get_guild(session.guild_id)
    if not guild:
        return
    state = MusicPanelState(
        session=session,
        bot=bot,
        owner_id=owner_id,
        panel_url=session.public_url(),
        guild=guild,
        notice=notice,
    )
    from ui.views.music_panel_layout_view import MusicPanelLayoutView

    try:
        view = MusicPanelLayoutView(None, state)
        await msg.edit(content=None, embed=None, view=view)
    except discord.HTTPException:
        session.clear_panel_binding()


async def refresh_bound_panel(session: "GuildMusicSession", bot: commands.Bot) -> None:
    await refresh_panel_message(session, bot)
