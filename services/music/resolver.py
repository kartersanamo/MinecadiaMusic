from __future__ import annotations

import logging
import re
from typing import List, Optional, Union
from urllib.parse import parse_qs, urlparse

import wavelink

from core.errors.exceptions import UserFacingError
from services.music.search_results import SearchResult, playlist_result, track_result

log = logging.getLogger("Music")

_URL_RE = re.compile(r"^https?://", re.I)
_YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^&\s]+&)*v=|embed/|v/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.I,
)
_SEARCH_PREFIXES = ("ytmsearch:", "ytsearch:", "scsearch:")


def is_url(query: str) -> bool:
    try:
        p = urlparse(query.strip())
        return bool(p.scheme and p.netloc)
    except ValueError:
        return False


def _youtube_video_id(url: str) -> str | None:
    match = _YT_VIDEO_ID_RE.search(url.strip())
    return match.group(1) if match else None


def youtube_video_only_url(url: str) -> str:
    """Strip playlist/radio params so Lavalink loads a single video."""
    video_id = _youtube_video_id(url)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url.strip()


def is_youtube_playlist_url(url: str) -> bool:
    lower = url.strip().lower()
    if "/playlist" in lower:
        return True
    parsed = urlparse(lower)
    params = parse_qs(parsed.query)
    if "list" in params and "v" not in params:
        return True
    return False


async def _load_url(url: str) -> wavelink.Search:
    """Load a direct URL, with YouTube fallback via ytsearch when embed/direct load fails."""
    try:
        return await _search(url)
    except wavelink.LavalinkLoadException as exc:
        video_id = _youtube_video_id(url)
        if not video_id:
            raise exc
        log.info(
            "Direct YouTube URL failed for %s; retrying via ytsearch",
            video_id,
        )
        try:
            return await _search(video_id, source=wavelink.TrackSource.YouTube)
        except wavelink.LavalinkLoadException:
            raise exc


async def _resolve_identifier(ident: str) -> wavelink.Search:
    if is_url(ident):
        return await _load_url(ident)
    bare = _strip_search_prefix(ident)
    if re.fullmatch(r"[\w-]{11}", bare):
        return await _search(bare, source=wavelink.TrackSource.YouTube)
    return await _search(bare, source=wavelink.TrackSource.YouTube)


def _playables_from_search(
    result: wavelink.Search,
    *,
    streams_ok: bool = False,
) -> List[wavelink.Playable]:
    if isinstance(result, wavelink.Playlist):
        tracks = list(result.tracks)
    elif isinstance(result, list):
        tracks = result
    else:
        tracks = []
    if not tracks:
        return []
    if not streams_ok and tracks[0].is_stream:
        raise UserFacingError("Live streams are not supported.")
    return [t for t in tracks if streams_ok or not t.is_stream]


async def tracks_from_identifier(
    identifier: str,
    *,
    kind: str = "track",
) -> List[wavelink.Playable]:
    """Load track(s) for queue add. Use kind='playlist' to load an entire playlist."""
    ident = identifier.strip()
    if not ident:
        raise UserFacingError("Missing track identifier.")

    log.info("resolver.tracks_from_identifier kind=%s ident=%s", kind, ident[:120])

    load_ident = ident
    if kind == "track" and is_url(ident):
        load_ident = youtube_video_only_url(ident)

    result = await _resolve_identifier(load_ident)
    tracks = _playables_from_search(result)

    if not tracks:
        raise UserFacingError("No results found for that query.")

    if kind == "playlist":
        if isinstance(result, wavelink.Playlist):
            log.info("resolver.loaded_playlist tracks=%s", len(tracks))
            return tracks
        raise UserFacingError(
            "That link is a single track, not a playlist. Use Add for one song."
        )

    return [tracks[0]]


def _strip_search_prefix(query: str) -> str:
    """Avoid ytmsearch:ytsearch:... when Lavalink MUSIC client re-wraps prefixed queries."""
    q = query.strip()
    lower = q.lower()
    for prefix in _SEARCH_PREFIXES:
        if lower.startswith(prefix):
            return q[len(prefix) :].strip()
    return q


async def _search(
    query: str,
    source: Optional[wavelink.TrackSource | str] = None,
) -> wavelink.Search:
    if source is None:
        return await wavelink.Playable.search(query)
    return await wavelink.Playable.search(query, source=source)


def _tracks_from_result(
    result: wavelink.Search,
) -> List[wavelink.Playable]:
    if isinstance(result, wavelink.Playlist):
        return list(result.tracks)
    if isinstance(result, list):
        return result
    return []


async def search_media(query: str, *, limit: int = 10) -> list[SearchResult]:
    """Search and return structured track/playlist results for UI."""
    q = _strip_search_prefix(query.strip())
    if not q:
        return []

    log.info("resolver.search_media query=%r limit=%s", q[:120], limit)

    if is_url(q):
        if is_youtube_playlist_url(q):
            try:
                result = await _load_url(q)
            except wavelink.LavalinkLoadException:
                return []
            if isinstance(result, wavelink.Playlist) and result.tracks:
                return [playlist_result(result, identifier=q)]
            return []
        try:
            result = await _load_url(q)
        except wavelink.LavalinkLoadException:
            return []
        if isinstance(result, wavelink.Playlist) and result.tracks:
            return [playlist_result(result, identifier=q)]
        if isinstance(result, list):
            return [
                track_result(t)
                for t in result[:limit]
                if not t.is_stream
            ]
        return []

    result = await _search(q, source=wavelink.TrackSource.YouTube)
    if isinstance(result, wavelink.Playlist) and result.tracks:
        return [playlist_result(result, identifier=q)]

    tracks = _tracks_from_result(result)
    if not tracks:
        result = await _search(q, source=wavelink.TrackSource.SoundCloud)
        if isinstance(result, wavelink.Playlist) and result.tracks:
            return [playlist_result(result, identifier=q)]
        tracks = _tracks_from_result(result)

    return [
        track_result(t)
        for t in tracks[:limit]
        if not t.is_stream
    ]


async def resolve_query(
    query: str,
    *,
    source: wavelink.TrackSource | str = wavelink.TrackSource.YouTube,
) -> Union[List[wavelink.Playable], wavelink.Playlist]:
    q = query.strip()
    if not q:
        raise UserFacingError("Please provide a search term or URL.")

    log.info("resolver.resolve_query query=%r source=%s", q[:120], source)

    if is_url(q):
        try:
            result = await _load_url(q)
        except wavelink.LavalinkLoadException as exc:
            raise UserFacingError(
                "Could not load that YouTube link. The video may be age-restricted or "
                "blocked — try searching by song name instead, or use a SoundCloud link."
            ) from exc
        if isinstance(result, wavelink.Playlist):
            if not result.tracks:
                raise UserFacingError("Playlist is empty or could not be loaded.")
            return result
        if isinstance(result, list):
            if not result:
                raise UserFacingError("No results found for that query.")
            if result[0].is_stream:
                raise UserFacingError("Live streams are not supported.")
            return result
        raise UserFacingError("Could not resolve that track.")

    bare = _strip_search_prefix(q)
    result = await _search(bare, source=source)
    if isinstance(result, wavelink.Playlist):
        if not result.tracks:
            raise UserFacingError("Playlist is empty or could not be loaded.")
        return result
    tracks = _tracks_from_result(result)
    if not tracks:
        log.info("YouTube search empty for %r; trying SoundCloud", bare)
        result = await _search(bare, source=wavelink.TrackSource.SoundCloud)
        if isinstance(result, wavelink.Playlist):
            if not result.tracks:
                raise UserFacingError("Playlist is empty or could not be loaded.")
            return result
        tracks = _tracks_from_result(result)
    if not tracks:
        raise UserFacingError(
            "No results found. YouTube search may be blocked on this server — "
            "try a direct SoundCloud URL or restart Lavalink after OAuth setup."
        )
    if tracks[0].is_stream:
        raise UserFacingError("Live streams are not supported.")
    return tracks


async def search_tracks(query: str, *, limit: int = 10) -> List[wavelink.Playable]:
    """Legacy flat track list — prefer search_media for UI."""
    items = await search_media(query, limit=limit)
    tracks: list[wavelink.Playable] = []
    for item in items:
        if item.kind == "playlist":
            continue
        result = await _resolve_identifier(item.identifier)
        loaded = _playables_from_search(result)
        if loaded:
            tracks.append(loaded[0])
    return tracks
