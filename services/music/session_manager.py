from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import discord
import wavelink
from discord.ext import commands
from wavelink.enums import AutoPlayMode, QueueMode
from wavelink.exceptions import QueueEmpty

from core.errors.exceptions import UserFacingError
from core.config import ConfigManager
from core.action_log import log_action, log_music_method
from repositories.music_panel_repository import MusicPanelRepository
from services.music.permissions import can_control, can_queue, in_voice_channel, require_voice
from services.music.queue import LoopMode, TrackInfo, _format_ms
from services.music.resolver import resolve_query

log = logging.getLogger("Music")

_ACTIVITY_MAX = 6


def _music_cfg() -> dict:
    return ConfigManager.all().get("MUSIC", {})


class GuildMusicSession:
    def __init__(self, guild_id: int, manager: "MusicSessionManager"):
        self.guild_id = guild_id
        self.manager = manager
        self.session_id = str(uuid.uuid4())
        self.session_token = secrets.token_urlsafe(32)
        self.created_at = time.time()
        self.panel_creator_id: Optional[int] = None
        self.text_channel_id: Optional[int] = None
        self.panel_message: Optional[discord.Message] = None
        self.panel_owner_id: Optional[int] = None
        self._panel_refresh_task: Optional[asyncio.Task] = None
        self.loop_mode = LoopMode.OFF
        self._last_track: Optional[wavelink.Playable] = None
        self._ws_callbacks: list[Callable[[], Awaitable[None] | None]] = []
        self.oauth_users: dict[int, float] = {}
        self._activity_log: list[dict[str, Any]] = []
        self._persist_task: Optional[asyncio.Task] = None
        self._queue_page = 0
        self._search_cache_key: Optional[str] = None
        self._search_cache_results: list[Any] = []

    async def get_search_page(
        self,
        query: str,
        *,
        page: int = 0,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        from services.music.resolver import SEARCH_PAGE_SIZE, search_media_all

        q = query.strip()
        size = page_size or int(_music_cfg().get("SEARCH_PAGE_SIZE", SEARCH_PAGE_SIZE))
        size = max(1, min(size, 40))
        if self._search_cache_key != q:
            self._search_cache_results = await search_media_all(q)
            self._search_cache_key = q
        page = max(0, page)
        total = len(self._search_cache_results)
        start = page * size
        page_items = self._search_cache_results[start : start + size]
        total_pages = max(1, (total + size - 1) // size) if total else 0
        return {
            "results": page_items,
            "page": page,
            "pageSize": size,
            "total": total,
            "totalPages": total_pages,
            "hasMore": start + size < total,
            "query": q,
        }

    def _queue_length(self) -> int:
        player = self.get_player()
        if not player:
            return 0
        return len(player.queue)

    def clamp_queue_page(self, page_size: int = 8) -> None:
        length = self._queue_length()
        if length <= 0:
            self._queue_page = 0
            return
        max_page = max(0, (length - 1) // page_size)
        self._queue_page = max(0, min(self._queue_page, max_page))

    def bump_queue_page(self, delta: int, page_size: int = 8) -> None:
        length = self._queue_length()
        max_page = max(0, (length - 1) // page_size) if length else 0
        self._queue_page = max(0, min(self._queue_page + delta, max_page))

    @property
    def queue_page(self) -> int:
        return self._queue_page

    def _member_display_name(self, user_id: int) -> str:
        guild = self.manager.bot.get_guild(self.guild_id)
        if guild:
            member = guild.get_member(user_id)
            if member:
                return member.display_name
        return f"User {user_id}"

    def clear_activity(self) -> None:
        self._activity_log.clear()

    def log_activity(self, actor_id: int, text: str) -> None:
        log_action(
            log,
            "session.activity",
            guild_id=self.guild_id,
            session_id=self.session_id[:8],
            actor_id=actor_id,
            text=text,
        )
        self._activity_log.append(
            {
                "actorId": str(actor_id),
                "actorName": self._member_display_name(actor_id),
                "text": text,
                "at": time.time(),
            }
        )
        if len(self._activity_log) > _ACTIVITY_MAX:
            self._activity_log = self._activity_log[-_ACTIVITY_MAX:]
        self.schedule_persist()

    def _enrich_track_dict(self, track: dict[str, Any]) -> dict[str, Any]:
        rid = track.get("requesterId")
        if rid:
            track = {**track, "requesterName": self._member_display_name(int(rid))}
        return track

    def is_expired(self) -> bool:
        ttl = int(os.getenv("MUSIC_SESSION_TTL_SECONDS", "86400"))
        return time.time() - self.created_at > ttl

    def refresh_panel(self, user_id: int) -> str:
        old_id = self.session_id
        log_action(
            log,
            "session.refresh_panel",
            guild_id=self.guild_id,
            old_session_id=old_id[:8],
            user_id=user_id,
        )
        self.session_id = str(uuid.uuid4())
        self.session_token = secrets.token_urlsafe(32)
        self.created_at = time.time()
        self.panel_creator_id = user_id
        self.clear_activity()
        self.manager._by_session_id.pop(old_id, None)
        self.manager._by_session_id[self.session_id] = self
        self.schedule_persist()
        asyncio.create_task(self.notify())
        return self.public_url()

    def public_url(self) -> str:
        base = os.getenv("MUSIC_PUBLIC_BASE_URL", "http://127.0.0.1:8790").rstrip("/")
        return f"{base}/{self.session_id}?token={self.session_token}"

    def subscribe(self, cb: Callable[[], Awaitable[None] | None]) -> None:
        self._ws_callbacks.append(cb)

    def bind_panel_message(self, message: discord.Message, owner_id: int) -> None:
        log_action(
            log,
            "session.bind_panel",
            guild_id=self.guild_id,
            session_id=self.session_id[:8],
            channel_id=message.channel.id,
            message_id=message.id,
            owner_id=owner_id,
        )
        self.panel_message = message
        self.panel_owner_id = owner_id
        self.text_channel_id = message.channel.id
        self.schedule_persist()

    def clear_panel_binding(self) -> None:
        log_action(
            log,
            "session.clear_panel",
            guild_id=self.guild_id,
            session_id=self.session_id[:8],
        )
        self.panel_message = None
        self.panel_owner_id = None
        self.text_channel_id = None
        if self._panel_refresh_task and not self._panel_refresh_task.done():
            self._panel_refresh_task.cancel()
        self._panel_refresh_task = None
        asyncio.create_task(self.manager.panel_repo.delete(self.guild_id))

    def schedule_persist(self) -> None:
        if not self.panel_message or not self.panel_owner_id:
            return
        if self._persist_task and not self._persist_task.done():
            self._persist_task.cancel()
        self._persist_task = asyncio.create_task(self._persist_panel())

    async def _persist_panel(self) -> None:
        try:
            await asyncio.sleep(0.25)
            if not self.panel_message or not self.panel_owner_id:
                return
            await self.manager.panel_repo.upsert(
                guild_id=self.guild_id,
                channel_id=self.panel_message.channel.id,
                message_id=self.panel_message.id,
                owner_id=self.panel_owner_id,
                session_id=self.session_id,
                session_token=self.session_token,
                panel_creator_id=self.panel_creator_id,
                loop_mode=self.loop_mode.value,
                activity_log=[],
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("Failed to persist music panel", exc_info=True)

    def apply_persisted_row(self, row: dict[str, Any]) -> None:
        self.manager._by_session_id.pop(self.session_id, None)
        self.session_id = str(row["session_id"])
        self.session_token = str(row["session_token"])
        self.panel_owner_id = int(row["owner_id"])
        self.panel_creator_id = (
            int(row["panel_creator_id"]) if row.get("panel_creator_id") else None
        )
        self.text_channel_id = int(row["channel_id"])
        self.loop_mode = LoopMode(str(row.get("loop_mode") or "off"))
        self.clear_activity()
        self.manager._register_session(self)

    def _schedule_panel_refresh(self) -> None:
        if not self.panel_message:
            return
        if self._panel_refresh_task and not self._panel_refresh_task.done():
            self._panel_refresh_task.cancel()
        self._panel_refresh_task = asyncio.create_task(self._debounced_panel_refresh())

    async def _debounced_panel_refresh(self) -> None:
        try:
            await asyncio.sleep(1.0)
            from ui.views.music_panel_support import refresh_bound_panel

            await refresh_bound_panel(self, self.manager.bot)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("Panel refresh failed", exc_info=True)

    async def notify(self) -> None:
        for cb in list(self._ws_callbacks):
            try:
                result = cb()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.debug("WebSocket notify callback failed", exc_info=True)
        self._schedule_panel_refresh()

    def get_player(self) -> Optional[wavelink.Player]:
        guild = self.manager.bot.get_guild(self.guild_id)
        if not guild or not guild.voice_client:
            return None
        if isinstance(guild.voice_client, wavelink.Player):
            return guild.voice_client
        return None

    @log_music_method("ensure_player")
    async def ensure_player(self, channel: discord.VoiceChannel) -> wavelink.Player:
        guild = channel.guild
        player = self.get_player()

        if player:
            if player.channel and player.channel.id != channel.id:
                active_name = player.channel.name
                raise UserFacingError(
                    f"The bot is already in an active music session in **{active_name}**. "
                    "Join that voice channel, or stop playback there before starting a new one."
                )
            player.autoplay = AutoPlayMode.partial
            self._sync_queue_mode(player)
            return player

        vc = guild.voice_client
        if vc and not isinstance(vc, wavelink.Player):
            await vc.disconnect(force=True)

        player = await channel.connect(cls=wavelink.Player, self_deaf=True)
        vol = int(_music_cfg().get("DEFAULT_VOLUME", 100))
        await player.set_volume(vol)
        player.autoplay = AutoPlayMode.partial
        self._sync_queue_mode(player)
        await self._on_voice_connected(channel)
        return player

    async def _on_voice_connected(self, channel: discord.VoiceChannel) -> None:
        try:
            from ui.views.music_panel_support import refresh_bound_panel

            await refresh_bound_panel(self, self.manager.bot)
        except Exception:
            log.debug("Failed to refresh panel after voice connect", exc_info=True)

    async def _clear_voice_channel_status(self, channel_id: int | None) -> None:
        if not channel_id:
            return
        try:
            await self.manager.bot.http.edit_voice_channel_status(
                None,
                channel_id=channel_id,
            )
        except Exception:
            log.debug("Failed to clear voice channel status", exc_info=True)

    def _sync_queue_mode(self, player: wavelink.Player) -> None:
        if self.loop_mode == LoopMode.TRACK:
            player.queue.mode = QueueMode.loop
        elif self.loop_mode == LoopMode.QUEUE:
            player.queue.mode = QueueMode.loop_all
        else:
            player.queue.mode = QueueMode.normal

    @log_music_method("skip")
    async def skip(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected to voice.")
        skipped = await player.skip(force=True)
        if actor_id:
            if skipped:
                self.log_activity(actor_id, f"skipped **{skipped.title}**")
            else:
                self.log_activity(actor_id, "skipped the current track")
        # Partial autoplay advances the queue on TrackEndEvent — playing again here races
        # autoplay, skips an extra queue item, and leaves player.current out of sync with audio.
        if skipped:
            return f"Skipped **{skipped.title}**."
        return "Skipped."

    def state_dict(self) -> dict[str, Any]:
        player = self.get_player()
        current = None
        queue_items: list[dict] = []
        position = 0
        paused = False
        playing = False
        volume = int(_music_cfg().get("DEFAULT_VOLUME", 100))
        channel_name = None
        voice_channel_id = None

        if player:
            paused = player.paused
            playing = player.playing
            volume = player.volume or volume
            if player.channel:
                channel_name = player.channel.name
                voice_channel_id = player.channel.id
            if player.current:
                current = self._enrich_track_dict(
                    TrackInfo.from_playable(player.current).to_dict()
                )
                position = player.position or 0
            for t in player.queue:
                queue_items.append(
                    self._enrich_track_dict(TrackInfo.from_playable(t).to_dict())
                )

        return {
            "guildId": str(self.guild_id),
            "sessionId": self.session_id,
            "loopMode": self.loop_mode.value,
            "current": current,
            "queue": queue_items,
            "positionMs": position,
            "paused": paused,
            "playing": playing,
            "volume": volume,
            "voiceChannel": channel_name,
            "voiceChannelId": str(voice_channel_id) if voice_channel_id else None,
            "panelUrl": self.public_url(),
            "panelCreatorId": self.panel_creator_id,
            "activity": list(self._activity_log),
        }

    @log_music_method("add_tracks")
    async def add_tracks(
        self,
        tracks: list[wavelink.Playable],
        requester_id: int,
        *,
        connect_member: discord.Member | None = None,
        playlist_title: str | None = None,
    ) -> tuple[int, bool]:
        if not tracks:
            raise UserFacingError("No tracks to add.")
        player = self.get_player()
        if not player and connect_member:
            channel = require_voice(connect_member)
            if not channel:
                raise UserFacingError(
                    "Join a voice channel in Discord first, then try again."
                )
            player = await self.ensure_player(channel)
        if not player:
            raise UserFacingError(
                "Bot is not connected to a voice channel. "
                "Join a voice channel in Discord, or use **/music** and tap **Join VC**."
            )

        max_len = int(_music_cfg().get("MAX_QUEUE_LENGTH", 200))
        if len(player.queue) + len(tracks) > max_len:
            raise UserFacingError(f"Queue cannot exceed {max_len} tracks.")

        for t in tracks:
            t.extras = {"requester_id": requester_id}

        started = False
        if not player.playing and not player.current:
            await player.play(tracks[0])
            for t in tracks[1:]:
                player.queue.put(t)
            started = True
        else:
            for t in tracks:
                player.queue.put(t)

        if len(tracks) == 1:
            title = tracks[0].title or "Unknown"
            if started:
                self.log_activity(requester_id, f"started **{title}**")
            else:
                self.log_activity(requester_id, f"added **{title}** to the queue")
        elif len(tracks) > 1:
            if playlist_title:
                self.log_activity(
                    requester_id,
                    f"added playlist **{playlist_title}** ({len(tracks)} tracks)",
                )
            else:
                self.log_activity(requester_id, f"added **{len(tracks)} tracks** to the queue")

        self.clamp_queue_page()
        await self.notify()
        return len(tracks), started

    @log_music_method("play_query")
    async def play_query(self, member: discord.Member, query: str) -> str:
        channel = require_voice(member)
        if not channel:
            raise UserFacingError("Join a voice channel first.")
        await self.ensure_player(channel)
        result = await resolve_query(query)
        tracks: list[wavelink.Playable] = []
        if isinstance(result, list):
            tracks = result[:1]
        else:
            tracks = list(result.tracks)
        count, started = await self.add_tracks(tracks, member.id)
        if started:
            return f"Now playing **{tracks[0].title}**."
        return f"Added **{tracks[0].title}** to the queue ({count} track(s))."

    @log_music_method("pause")
    async def pause(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected to voice.")
        if not player.playing:
            return "Nothing is playing."
        if player.paused:
            return "Playback is already paused."
        await player.pause(True)
        if actor_id:
            self.log_activity(actor_id, "paused playback")
        await self.notify()
        return "Paused playback."

    @log_music_method("resume")
    async def resume(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected to voice.")
        if not player.paused:
            return "Playback is already running."
        await player.pause(False)
        if actor_id:
            self.log_activity(actor_id, "resumed playback")
        await self.notify()
        return "Resumed playback."

    @log_music_method("seek")
    async def seek(self, position_ms: int, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected to voice.")
        if not player.current:
            raise UserFacingError("Nothing is playing.")
        duration = player.current.length or 0
        if duration <= 0:
            raise UserFacingError("Cannot seek during a live stream.")
        position_ms = max(0, min(int(position_ms), duration))
        await player.seek(position_ms)
        if actor_id:
            self.log_activity(actor_id, f"seeked to `{_format_ms(position_ms)}`")
        await self.notify()
        return f"Seeked to **{_format_ms(position_ms)}**."

    @log_music_method("restart")
    async def restart(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected to voice.")
        if not player.current:
            raise UserFacingError("Nothing is playing.")
        if (player.current.length or 0) <= 0:
            raise UserFacingError("Cannot restart a live stream.")
        await player.seek(0)
        if actor_id:
            title = player.current.title or "track"
            self.log_activity(actor_id, f"restarted **{title}**")
        await self.notify()
        return f"Restarted **{player.current.title or 'track'}**."

    @log_music_method("stop")
    async def stop(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        voice_channel_id = player.channel.id if player and player.channel else None
        if player:
            player.queue.clear()
            await player.stop()
            await player.disconnect()
        await self._clear_voice_channel_status(voice_channel_id)
        self.loop_mode = LoopMode.OFF
        self._last_track = None
        if actor_id:
            self.log_activity(actor_id, "stopped playback and disconnected")
        await self.notify()
        self.clear_panel_binding()
        return "Stopped and disconnected."

    @log_music_method("set_volume")
    async def set_volume(self, level: int, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected to voice.")
        level = max(0, min(150, level))
        await player.set_volume(level)
        if actor_id:
            self.log_activity(actor_id, f"set volume to **{level}%**")
        await self.notify()
        return f"Volume set to **{level}%**."

    @log_music_method("shuffle_queue")
    async def shuffle_queue(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player or player.queue.is_empty:
            raise UserFacingError("Queue is empty.")
        import random

        items = list(player.queue)
        random.shuffle(items)
        player.queue.clear()
        for t in items:
            player.queue.put(t)
        if actor_id:
            self.log_activity(actor_id, "shuffled the queue")
        await self.notify()
        return "Shuffled the queue."

    @log_music_method("remove_at")
    async def remove_at(self, index: int, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected.")
        if index < 0 or index >= len(player.queue):
            raise UserFacingError("Invalid queue position.")
        removed = player.queue.peek(index)
        player.queue.delete(index)
        if actor_id:
            self.log_activity(
                actor_id,
                f"removed **{removed.title or 'Unknown'}** from the queue",
            )
        self.clamp_queue_page()
        await self.notify()
        return f"Removed **{removed.title}** from the queue."

    @log_music_method("remove_many")
    async def remove_many(self, indices: list[int], *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected.")
        if not indices:
            raise UserFacingError("Nothing selected to remove.")
        unique = sorted({int(i) for i in indices}, reverse=True)
        removed_titles: list[str] = []
        for index in unique:
            if index < 0 or index >= len(player.queue):
                raise UserFacingError("Invalid queue position.")
            track = player.queue.peek(index)
            removed_titles.append(track.title or "Unknown")
            player.queue.delete(index)
        if actor_id:
            if len(removed_titles) == 1:
                self.log_activity(
                    actor_id,
                    f"removed **{removed_titles[0]}** from the queue",
                )
            else:
                self.log_activity(
                    actor_id,
                    f"removed **{len(removed_titles)} tracks** from the queue",
                )
        self.clamp_queue_page()
        await self.notify()
        if len(removed_titles) == 1:
            return f"Removed **{removed_titles[0]}** from the queue."
        return f"Removed **{len(removed_titles)}** tracks from the queue."

    @log_music_method("clear_queue")
    async def clear_queue(self, *, actor_id: int | None = None) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected.")
        if player.queue.is_empty:
            raise UserFacingError("Queue is already empty.")
        count = len(player.queue)
        player.queue.clear()
        self._queue_page = 0
        if actor_id:
            self.log_activity(actor_id, f"cleared the queue (**{count}** tracks)")
        await self.notify()
        return f"Cleared **{count}** tracks from the queue."

    @log_music_method("move_track")
    async def move_track(
        self,
        from_index: int,
        to_index: int,
        *,
        actor_id: int | None = None,
    ) -> str:
        player = self.get_player()
        if not player:
            raise UserFacingError("Not connected.")
        track = player.queue.peek(from_index)
        player.queue.delete(from_index)
        player.queue.put_at(to_index, track)
        if actor_id:
            self.log_activity(actor_id, f"moved **{track.title or 'Unknown'}** in the queue")
        await self.notify()
        return f"Moved **{track.title}**."

    @log_music_method("set_loop")
    async def set_loop(self, mode: LoopMode, *, actor_id: int | None = None) -> str:
        self.loop_mode = mode
        player = self.get_player()
        if player:
            self._sync_queue_mode(player)
        if actor_id:
            self.log_activity(actor_id, f"set loop to **{mode.value}**")
        self.schedule_persist()
        await self.notify()
        return f"Loop mode set to **{mode.value}**."

    @log_music_method("track_end")
    async def handle_track_end(self, player: wavelink.Player | None, track: wavelink.Playable | None) -> None:
        if not player:
            await self.notify()
            return
        if player.queue.is_empty and not player.playing:
            idle = int(_music_cfg().get("IDLE_DISCONNECT_SECONDS", 300))
            log_action(
                log,
                "session.idle_wait",
                level=logging.DEBUG,
                guild_id=self.guild_id,
                session_id=self.session_id[:8],
                seconds=idle,
            )
            await asyncio.sleep(idle)
            current = self.get_player()
            if (
                current
                and current.queue.is_empty
                and not current.playing
                and current.channel
            ):
                voice_channel_id = current.channel.id
                log_action(
                    log,
                    "session.idle_disconnect",
                    guild_id=self.guild_id,
                    session_id=self.session_id[:8],
                    channel_id=voice_channel_id,
                )
                try:
                    await current.disconnect()
                except Exception:
                    log.debug("Idle disconnect failed", exc_info=True)
                await self._clear_voice_channel_status(voice_channel_id)
        await self.notify()


class MusicSessionManager:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, GuildMusicSession] = {}
        self._by_session_id: dict[str, GuildMusicSession] = {}
        self._lavalink_ready = False
        self.panel_repo = MusicPanelRepository()

    async def restore_panels(self) -> None:
        rows = await self.panel_repo.fetch_all()
        if not rows:
            return
        from ui.views.music_panel_support import refresh_bound_panel

        restored = 0
        for row in rows:
            guild_id = int(row["guild_id"])
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            session = self.get_session(guild_id)
            session.apply_persisted_row(row)
            channel = guild.get_channel(int(row["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                await self.panel_repo.delete(guild_id)
                session.clear_panel_binding()
                continue
            try:
                message = await channel.fetch_message(int(row["message_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await self.panel_repo.delete(guild_id)
                session.clear_panel_binding()
                continue
            session.panel_message = message
            restored += 1
            try:
                await refresh_bound_panel(session, self.bot)
            except discord.HTTPException:
                log.debug("Could not refresh restored panel for guild %s", guild_id, exc_info=True)
        if restored:
            log.info("Restored %s music panel(s) from database", restored)

    async def connect_lavalink(self) -> None:
        if self._lavalink_ready:
            return
        host = os.getenv("LAVALINK_HOST", "127.0.0.1")
        port = int(os.getenv("LAVALINK_PORT", "2333"))
        password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
        uri = f"http://{host}:{port}"
        try:
            node = wavelink.Node(
                uri=uri,
                password=password,
                inactive_channel_tokens=None,
            )
            await wavelink.Pool.connect(client=self.bot, nodes=[node])
            self._lavalink_ready = True
            log.info("Connected to Lavalink at %s", uri)
        except Exception as exc:
            log.error("Failed to connect to Lavalink: %s", exc, exc_info=True)
            raise

    def get_session(self, guild_id: int) -> GuildMusicSession:
        if guild_id not in self.sessions:
            session = GuildMusicSession(guild_id, self)
            self.sessions[guild_id] = session
            self._register_session(session)
            log_action(
                log,
                "manager.create_session",
                guild_id=guild_id,
                session_id=session.session_id[:8],
            )
        return self.sessions[guild_id]

    def _register_session(self, session: GuildMusicSession) -> None:
        self._by_session_id[session.session_id] = session

    def get_by_session_id(self, session_id: str) -> Optional[GuildMusicSession]:
        session = self._by_session_id.get(session_id)
        if session and session.is_expired():
            return None
        return session

    def validate_token(self, session_id: str, token: str) -> Optional[GuildMusicSession]:
        session = self.get_by_session_id(session_id)
        if not session or not secrets.compare_digest(session.session_token, token):
            log_action(
                log,
                "manager.invalid_token",
                level=logging.DEBUG,
                session_id=session_id[:8] if session_id else None,
                found=bool(session),
            )
            return None
        return session

    async def register_events(self) -> None:
        @self.bot.listen()
        async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload) -> None:
            player = payload.player
            if not player or not player.guild:
                return
            session = self.sessions.get(player.guild.id)
            if session and payload.track:
                log_action(
                    log,
                    "lavalink.track_start",
                    level=logging.DEBUG,
                    guild_id=player.guild.id,
                    session_id=session.session_id[:8],
                    track=payload.track.title,
                )
                session._last_track = payload.track
                await session.notify()

        @self.bot.listen()
        async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload) -> None:
            player = payload.player
            if not player or not player.guild:
                return
            session = self.sessions.get(player.guild.id)
            if session:
                log_action(
                    log,
                    "lavalink.track_end",
                    level=logging.DEBUG,
                    guild_id=player.guild.id,
                    session_id=session.session_id[:8],
                    track=getattr(payload.track, "title", None),
                    reason=getattr(payload, "reason", None),
                )
                await session.handle_track_end(player, payload.track)
                if payload.track:
                    session._last_track = payload.track

        @self.bot.listen()
        async def on_wavelink_track_exception(
            payload: wavelink.TrackExceptionEventPayload,
        ) -> None:
            player = payload.player
            guild_id = player.guild.id if player and player.guild else None
            exc = payload.exception or {}
            message = str(exc.get("message", ""))
            cause = str(exc.get("cause", ""))
            log.warning(
                "Track failed to play (guild=%s): %s — %s",
                guild_id or "?",
                message[:120],
                cause[:200],
            )
            if not player or not player.guild:
                return
            session = self.sessions.get(player.guild.id)
            if "login" in message.lower() or "All clients failed" in cause:
                log.warning(
                    "YouTube blocked playback — complete Lavalink OAuth setup"
                )
            try:
                await player.skip(force=True)
            except Exception:
                pass
            if session:
                await session.notify()

    def check_member_web(
        self,
        session: GuildMusicSession,
        user_id: int,
        *,
        need_control: bool = False,
        need_queue: bool = False,
    ) -> discord.Member:
        guild = self.bot.get_guild(session.guild_id)
        if not guild:
            raise UserFacingError("Guild not available.")
        member = guild.get_member(user_id)
        if not member:
            raise UserFacingError("You must be in this Discord server.")
        player = session.get_player()
        vc_id = player.channel.id if player and player.channel else None
        if need_control or need_queue:
            if not vc_id:
                raise UserFacingError(
                    "The bot is not in a voice channel — join a VC and use **Join VC** in Discord first."
                )
            if not in_voice_channel(member, vc_id):
                channel_name = player.channel.name if player and player.channel else "voice channel"
                raise UserFacingError(
                    f"Join **{channel_name}** in Discord to control this session."
                )
        if need_control and not can_control(member, voice_channel_id=vc_id):
            log_action(
                log,
                "manager.permission_denied",
                guild_id=session.guild_id,
                user_id=user_id,
                need="control",
                voice_channel_id=vc_id,
            )
            raise UserFacingError("You do not have permission to control playback.")
        if need_queue and not can_queue(member, voice_channel_id=vc_id):
            log_action(
                log,
                "manager.permission_denied",
                guild_id=session.guild_id,
                user_id=user_id,
                need="queue",
                voice_channel_id=vc_id,
            )
            raise UserFacingError("You do not have permission to modify the queue.")
        return member
