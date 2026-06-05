from __future__ import annotations

import os
from typing import Optional, Tuple
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from services.music.session_manager import GuildMusicSession, MusicSessionManager


def _bearer_token(request: web.Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query.get("token") or request.cookies.get("music_token")


def session_from_request(
    request: web.Request,
    manager: MusicSessionManager,
) -> Tuple[GuildMusicSession, str]:
    session_id = request.match_info.get("session_id") or request.match_info.get("id")
    if not session_id:
        raise web.HTTPBadRequest(text='{"error":"Missing session id"}', content_type="application/json")
    token = _bearer_token(request)
    if not token:
        raise web.HTTPUnauthorized(text='{"error":"Missing token"}', content_type="application/json")
    session = manager.validate_token(session_id, token)
    if not session:
        raise web.HTTPUnauthorized(text='{"error":"Invalid or expired session"}', content_type="application/json")
    return session, token


async def discord_oauth_exchange(code: str) -> dict:
    client_id = os.getenv("DISCORD_CLIENT_ID", "")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
    redirect = os.getenv(
        "DISCORD_OAUTH_REDIRECT_URI",
        "https://music.kartersanamo.com/oauth/callback",
    )
    if not client_id or not client_secret:
        raise ValueError("Discord OAuth not configured")
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
    }
    async with aiohttp.ClientSession() as http:
        async with http.post(
            "https://discord.com/api/oauth2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise ValueError(f"Token exchange failed: {body}")
            return await resp.json()


async def discord_fetch_user(access_token: str) -> dict:
    async with aiohttp.ClientSession() as http:
        async with http.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            if resp.status != 200:
                raise ValueError("Failed to fetch user")
            return await resp.json()


def oauth_authorize_url(state: str) -> str:
    client_id = os.getenv("DISCORD_CLIENT_ID", "")
    redirect = os.getenv(
        "DISCORD_OAUTH_REDIRECT_URI",
        "https://music.kartersanamo.com/oauth/callback",
    )
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "identify",
            "state": state,
        }
    )
    return f"https://discord.com/api/oauth2/authorize?{params}"
