"""Proxy track artwork for Discord Activity CSP compliance."""
from __future__ import annotations

import hashlib
import logging
import time
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

log = logging.getLogger("music_http")

# Hostnames Lavalink / YouTube / SoundCloud commonly use for artwork.
_ALLOWED_HOST_SUFFIXES = (
    "ytimg.com",
    "youtube.com",
    "googleusercontent.com",
    "ggpht.com",
    "sndcdn.com",
    "scdn.co",
    "soundcloud.com",
    "mzstatic.com",
    "spotifycdn.com",
    "discordapp.com",
    "discordapp.net",
    "discord.media",
)

_CACHE: dict[str, tuple[bytes, str, float]] = {}
_CACHE_TTL_SECONDS = 60 * 60 * 6
_CACHE_MAX = 512


def _host_allowed(hostname: str) -> bool:
    host = hostname.lower().strip(".")
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_HOST_SUFFIXES)


def validate_artwork_url(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise web.HTTPBadRequest(text="Invalid artwork URL scheme")
    if not parsed.hostname or not _host_allowed(parsed.hostname):
        raise web.HTTPBadRequest(text="Artwork host not allowed")
    return cleaned


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _prune_cache() -> None:
    if len(_CACHE) <= _CACHE_MAX:
        return
    now = time.time()
    expired = [key for key, (_, _, expires) in _CACHE.items() if expires <= now]
    for key in expired:
        _CACHE.pop(key, None)
    if len(_CACHE) > _CACHE_MAX:
        for key in list(_CACHE.keys())[: len(_CACHE) - _CACHE_MAX]:
            _CACHE.pop(key, None)


async def fetch_artwork(url: str) -> tuple[bytes, str]:
    key = _cache_key(url)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and cached[2] > now:
        return cached[0], cached[1]

    timeout = aiohttp.ClientTimeout(total=12)
    headers = {"User-Agent": "MinecadiaMusic/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
        async with http.get(url) as resp:
            if resp.status != 200:
                log.warning("Artwork fetch failed status=%s url=%s", resp.status, url[:120])
                raise web.HTTPBadGateway(text="Upstream artwork unavailable")
            body = await resp.read()
            if len(body) > 5 * 1024 * 1024:
                raise web.HTTPBadRequest(text="Artwork too large")
            content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            if not content_type.startswith("image/"):
                content_type = "image/jpeg"

    _CACHE[key] = (body, content_type, now + _CACHE_TTL_SECONDS)
    _prune_cache()
    return body, content_type


async def artwork_handler(request: web.Request) -> web.Response:
    raw = request.query.get("url", "").strip()
    if not raw:
        raise web.HTTPBadRequest(text="Missing url")
    url = validate_artwork_url(raw)
    body, content_type = await fetch_artwork(url)
    return web.Response(
        body=body,
        content_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
