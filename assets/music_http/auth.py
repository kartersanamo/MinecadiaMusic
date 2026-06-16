from __future__ import annotations

import logging
import os
from typing import Optional, Tuple
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from core.action_log import log_action
from core.loggers import log_http
from core.errors.exceptions import UserFacingError
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
        log_action(log_http, "auth.session_rejected", session_id=session_id[:8])
        raise web.HTTPUnauthorized(text='{"error":"Invalid or expired session"}', content_type="application/json")
    log_action(
        log_http,
        "auth.session_ok",
        level=logging.DEBUG,
        session_id=session_id[:8],
        guild_id=session.guild_id,
    )
    return session, token


def _oauth_client() -> tuple[str, str]:
    client_id = os.getenv("DISCORD_CLIENT_ID", "")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ValueError("Discord OAuth not configured")
    return client_id, client_secret


async def discord_oauth_exchange(code: str, *, redirect_uri: str | None = None) -> dict:
    client_id, client_secret = _oauth_client()
    redirect = redirect_uri
    if redirect is None:
        redirect = os.getenv(
            "DISCORD_OAUTH_REDIRECT_URI",
            "https://music.kartersanamo.com/oauth/callback",
        )
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


async def discord_oauth_exchange_activity(code: str) -> dict:
    """Exchange an Embedded App SDK authorize code (empty redirect_uri)."""
    return await discord_oauth_exchange(code, redirect_uri="")


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


async def activity_bootstrap_for_user(
    manager: MusicSessionManager,
    *,
    guild_id: int,
    user_id: int,
) -> dict[str, str]:
    import time

    session = manager.sessions.get(guild_id)
    if not session or not session.panel_owner_id:
        raise UserFacingError("No music panel for this server. Run **`/music`** first.")
    manager.check_member_web(session, user_id, need_queue=True)
    session.oauth_users[user_id] = time.time()
    log_action(
        log_http,
        "auth.activity_bootstrap",
        guild_id=guild_id,
        user_id=user_id,
        session_id=session.session_id[:8],
    )
    return {
        "sessionId": session.session_id,
        "token": session.session_token,
        "userId": str(user_id),
        "panelUrl": session.public_url(),
    }
