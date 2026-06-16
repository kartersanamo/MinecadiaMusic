from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import wavelink

SearchKind = Literal["track", "playlist"]

_YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^&\s]+&)*v=|embed/|v/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.I,
)


def _youtube_video_id(url: str) -> str | None:
    match = _YT_VIDEO_ID_RE.search(url.strip())
    return match.group(1) if match else None


def external_url(uri: str | None, identifier: str | None = None) -> str | None:
    """Public watch/page URL for a track or playlist when available."""
    for candidate in (uri, identifier):
        if not candidate:
            continue
        value = candidate.strip()
        video_id = _youtube_video_id(value)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        if value.startswith(("http://", "https://")):
            return value
    return None


def _safe_md_text(text: str, *, limit: int = 80) -> str:
    cleaned = (text or "Unknown").replace("[", "(").replace("]", ")")
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned


def markdown_link(
    title: str,
    uri: str | None,
    identifier: str | None = None,
    *,
    bold: bool = False,
) -> str:
    url = external_url(uri, identifier)
    label = _safe_md_text(title)
    if url:
        return f"[{label}]({url})"
    return f"**{label}**" if bold else label


def fallback_artwork(uri: str | None, identifier: str | None = None) -> str | None:
    """YouTube thumbnail when Lavalink does not populate artwork on search hits."""
    for candidate in (uri, identifier):
        if not candidate:
            continue
        video_id = _youtube_video_id(str(candidate))
        if video_id:
            return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return None


def _video_only_identifier(track: wavelink.Playable) -> str:
    if track.uri:
        video_id = _youtube_video_id(track.uri)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return track.identifier or track.uri or ""


@dataclass
class SearchResultTrack:
    title: str
    author: str
    uri: str | None
    identifier: str
    duration_ms: int
    artwork: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "uri": self.uri,
            "identifier": self.identifier,
            "linkUrl": external_url(self.uri, self.identifier),
            "durationMs": self.duration_ms,
            "durationText": _format_ms(self.duration_ms),
            "artwork": self.artwork,
        }


@dataclass
class SearchResult:
    kind: SearchKind
    title: str
    author: str
    uri: str | None
    identifier: str
    artwork: str | None
    duration_ms: int
    track_count: int
    tracks: list[SearchResultTrack]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "author": self.author,
            "uri": self.uri,
            "identifier": self.identifier,
            "linkUrl": external_url(self.uri, self.identifier),
            "artwork": self.artwork,
            "durationMs": self.duration_ms,
            "durationText": _format_ms(self.duration_ms) if self.duration_ms else None,
            "trackCount": self.track_count,
            "tracks": [t.to_dict() for t in self.tracks],
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


def playable_preview(track: wavelink.Playable) -> SearchResultTrack:
    ident = _video_only_identifier(track)
    artwork = track.artwork or fallback_artwork(track.uri, ident)
    return SearchResultTrack(
        title=track.title or "Unknown",
        author=track.author or "Unknown",
        uri=track.uri,
        identifier=ident,
        duration_ms=track.length or 0,
        artwork=artwork,
    )


def playlist_result(
    playlist: wavelink.Playlist,
    *,
    identifier: str,
) -> SearchResult:
    previews = [playable_preview(t) for t in playlist.tracks if not t.is_stream]
    title = playlist.name or (previews[0].title if previews else "Playlist")
    author = playlist.author or (previews[0].author if previews else "Unknown")
    return SearchResult(
        kind="playlist",
        title=title,
        author=author,
        uri=playlist.url or identifier,
        identifier=identifier,
        artwork=playlist.artwork
        or (previews[0].artwork if previews else None)
        or fallback_artwork(playlist.url, identifier),
        duration_ms=0,
        track_count=len(previews),
        tracks=previews,
    )


def track_result(track: wavelink.Playable) -> SearchResult:
    preview = playable_preview(track)
    return SearchResult(
        kind="track",
        title=preview.title,
        author=preview.author,
        uri=preview.uri,
        identifier=preview.identifier,
        artwork=preview.artwork,
        duration_ms=preview.duration_ms,
        track_count=1,
        tracks=[preview],
    )
