from __future__ import annotations

from typing import Optional

import discord

from core.config import ConfigManager


def _music_config() -> dict:
    return ConfigManager.all().get("MUSIC", {})


def _has_role(member: discord.Member, role_names: list[str]) -> bool:
    if "*" in role_names:
        return True
    names = {r.name for r in member.roles}
    return any(name in names for name in role_names)


def in_voice_channel(
    member: discord.Member,
    voice_channel_id: Optional[int],
) -> bool:
    if not voice_channel_id:
        return False
    if not member.voice or not member.voice.channel:
        return False
    return member.voice.channel.id == voice_channel_id


def can_open_panel(member: discord.Member) -> bool:
    cfg = _music_config()
    return _has_role(member, cfg.get("DJ_ROLES", ConfigManager.all().get("ADMIN_ROLES", [])))


def can_control(
    member: discord.Member,
    *,
    voice_channel_id: Optional[int] = None,
) -> bool:
    if not voice_channel_id:
        cfg = _music_config()
        return _has_role(member, cfg.get("DJ_ROLES", []))
    return in_voice_channel(member, voice_channel_id)


def can_queue(
    member: discord.Member,
    *,
    voice_channel_id: Optional[int] = None,
) -> bool:
    cfg = _music_config()
    if not _has_role(member, cfg.get("REQUEST_ROLES", ["*"])):
        return False
    if not voice_channel_id:
        return member.voice is not None
    return in_voice_channel(member, voice_channel_id)


def require_voice(member: discord.Member) -> Optional[discord.VoiceChannel]:
    if not member.voice or not member.voice.channel:
        return None
    ch = member.voice.channel
    if isinstance(ch, discord.VoiceChannel):
        return ch
    if isinstance(ch, discord.StageChannel):
        return ch
    return None
