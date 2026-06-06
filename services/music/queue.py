from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import wavelink

from services.music.search_results import external_url


class LoopMode(str, Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


@dataclass
class TrackInfo:
    title: str
    uri: Optional[str]
    duration_ms: int
    duration_text: str
    author: str
    artwork: Optional[str]
    requester_id: Optional[int]
    identifier: str
    is_stream: bool

    @classmethod
    def from_playable(cls, track: wavelink.Playable) -> "TrackInfo":
        requester = getattr(track.extras, "requester_id", None)
        if requester is None and hasattr(track.extras, "__getitem__"):
            try:
                requester = track.extras["requester_id"]
            except (KeyError, TypeError):
                requester = None
        return cls(
            title=track.title or "Unknown",
            uri=track.uri,
            duration_ms=track.length,
            duration_text=_format_ms(track.length),
            author=track.author or "Unknown",
            artwork=track.artwork,
            requester_id=int(requester) if requester is not None else None,
            identifier=track.identifier,
            is_stream=track.is_stream,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "uri": self.uri,
            "linkUrl": external_url(self.uri, self.identifier),
            "durationMs": self.duration_ms,
            "durationText": self.duration_text,
            "author": self.author,
            "artwork": self.artwork,
            "requesterId": str(self.requester_id) if self.requester_id else None,
            "identifier": self.identifier,
            "isStream": self.is_stream,
        }


def _format_ms(ms: int) -> str:
    if ms <= 0:
        return "LIVE" if ms == 0 else "?:??"
    seconds = ms // 1000
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def serialize_queue(player: wavelink.Player) -> list[dict[str, Any]]:
    items = []
    if player.current:
        items.append(TrackInfo.from_playable(player.current).to_dict())
    for t in player.queue:
        items.append(TrackInfo.from_playable(t).to_dict())
    return items
