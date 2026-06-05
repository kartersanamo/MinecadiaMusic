from __future__ import annotations

import logging
import re
from typing import List, Optional, Union
from urllib.parse import urlparse

import wavelink

from core.errors.exceptions import UserFacingError

log = logging.getLogger("Music")

_URL_RE = re.compile(r"^https?://", re.I)
_SEARCH_PREFIXES = ("ytmsearch:", "ytsearch:", "scsearch:")


def is_url(query: str) -> bool:
    try:
        p = urlparse(query.strip())
        return bool(p.scheme and p.netloc)
    except ValueError:
        return False


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


async def resolve_query(
    query: str,
    *,
    source: wavelink.TrackSource | str = wavelink.TrackSource.YouTube,
) -> Union[List[wavelink.Playable], wavelink.Playlist]:
    q = query.strip()
    if not q:
        raise UserFacingError("Please provide a search term or URL.")

    if is_url(q):
        try:
            result = await _search(q)
        except wavelink.LavalinkLoadException as exc:
            raise UserFacingError(
                "Could not load that URL. YouTube may be blocked — try a SoundCloud link, "
                "or re-check Lavalink OAuth (see docs/MUSIC_LAVALINK.md)."
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
    tracks = _tracks_from_result(await _search(bare, source=source))
    if not tracks:
        log.info("YouTube search empty for %r; trying SoundCloud", bare)
        tracks = _tracks_from_result(
            await _search(bare, source=wavelink.TrackSource.SoundCloud),
        )
    if not tracks:
        raise UserFacingError(
            "No results found. YouTube search may be blocked on this server — "
            "try a direct SoundCloud URL or restart Lavalink after OAuth setup."
        )
    if tracks[0].is_stream:
        raise UserFacingError("Live streams are not supported.")
    return tracks


async def search_tracks(query: str, *, limit: int = 10) -> List[wavelink.Playable]:
    q = _strip_search_prefix(query.strip())
    if not q:
        return []
    if is_url(q):
        result = await wavelink.Playable.search(q)
        if isinstance(result, list):
            return [t for t in result[:limit] if not t.is_stream]
        if isinstance(result, wavelink.Playlist):
            return [t for t in result.tracks[:limit] if not t.is_stream]
        return []
    tracks = _tracks_from_result(
        await _search(q, source=wavelink.TrackSource.YouTube),
    )
    if not tracks:
        tracks = _tracks_from_result(
            await _search(q, source=wavelink.TrackSource.SoundCloud),
        )
    return [t for t in tracks[:limit] if not t.is_stream]
