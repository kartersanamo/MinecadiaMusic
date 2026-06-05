from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web


from assets.music_http.auth import (
    discord_fetch_user,
    discord_oauth_exchange,
    oauth_authorize_url,
    session_from_request,
)
from core.errors.exceptions import UserFacingError
from services.music.queue import LoopMode
from services.music.resolver import search_tracks

if TYPE_CHECKING:
    from discord.ext import commands

log = logging.getLogger("music_http")
_server: web.AppRunner | None = None
_WEB_DIR = Path(__file__).resolve().parent.parent / "music_web"


def _music_port() -> int:
    return int(os.getenv("MUSIC_HTTP_PORT", "8790"))


def _api_error_response(exc: Exception, *, context: str) -> web.Response:
    if isinstance(exc, UserFacingError):
        return web.json_response({"ok": False, "error": exc.user_message}, status=400)
    if isinstance(exc, web.HTTPException):
        raise exc
    log.exception("%s failed", context)
    return web.json_response(
        {"ok": False, "error": "Something went wrong. Please try again."},
        status=500,
    )


def _parse_user_id(body: dict, session) -> int:
    uid = body.get("userId") or body.get("user_id")
    if uid:
        return int(uid)
    if session.oauth_users:
        return int(next(iter(session.oauth_users.keys())))
    raise web.HTTPUnauthorized(
        text='{"error":"Discord login required"}',
        content_type="application/json",
    )


async def start_music_http(bot: "commands.Bot") -> None:
    global _server
    manager = bot.app.music
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "lavalink": manager._lavalink_ready})

    async def serve_static(request: web.Request) -> web.Response:
        name = request.match_info.get("path", "styles.css")
        path = (_WEB_DIR / name).resolve()
        if not str(path).startswith(str(_WEB_DIR.resolve())):
            raise web.HTTPForbidden()
        if not path.is_file():
            raise web.HTTPNotFound()
        ctype = "application/javascript" if path.suffix == ".js" else "text/css"
        return web.Response(body=path.read_bytes(), content_type=ctype)

    async def serve_spa(request: web.Request) -> web.Response:
        index = _WEB_DIR / "index.html"
        if not index.is_file():
            raise web.HTTPNotFound()
        html = index.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def api_state(request: web.Request) -> web.Response:
        session, _ = session_from_request(request, manager)
        return web.json_response(session.state_dict())

    async def api_search(request: web.Request) -> web.Response:
        session, _ = session_from_request(request, manager)
        q = request.query.get("q", "").strip()
        if request.can_read_body and request.content_type == "application/json":
            body = await request.json()
            if not q:
                q = str(body.get("q", "")).strip()
            if body.get("userId") or body.get("user_id") or session.oauth_users:
                try:
                    uid = _parse_user_id(body, session)
                    manager.check_member_web(session, uid, need_control=True)
                except web.HTTPException:
                    raise
                except UserFacingError as exc:
                    return web.json_response({"error": exc.user_message}, status=403)
                except Exception as exc:
                    return _api_error_response(exc, context="api_search auth")
        if not q:
            return web.json_response({"results": []})
        tracks = await search_tracks(q, limit=10)
        results = []
        for t in tracks:
            results.append(
                {
                    "title": t.title,
                    "author": t.author,
                    "uri": t.uri,
                    "duration": t.length,
                    "artwork": t.artwork,
                    "identifier": t.identifier,
                }
            )
        return web.json_response({"results": results})

    async def api_queue_add(request: web.Request) -> web.Response:
        try:
            session, _ = session_from_request(request, manager)
            body = await request.json()
            uid = _parse_user_id(body, session)
            member = manager.check_member_web(session, uid, need_queue=True)
            query = body.get("query") or body.get("uri")
            if not query:
                return web.json_response({"ok": False, "error": "query or uri required"}, status=400)
            from services.music.resolver import resolve_query

            result = await resolve_query(str(query))
            tracks = result if isinstance(result, list) else list(result.tracks)
            for t in tracks:
                t.extras = {"requester_id": member.id}
            count, started = await session.add_tracks(tracks, member.id)
            return web.json_response({"ok": True, "added": count, "started": started})
        except Exception as exc:
            return _api_error_response(exc, context="api_queue_add")

    async def api_queue_remove(request: web.Request) -> web.Response:
        try:
            session, _ = session_from_request(request, manager)
            body = await request.json() if request.can_read_body else {}
            uid = _parse_user_id(body, session)
            manager.check_member_web(session, uid, need_control=True)
            index = int(request.match_info["index"])
            msg = await session.remove_at(index)
            return web.json_response({"ok": True, "message": msg})
        except Exception as exc:
            return _api_error_response(exc, context="api_queue_remove")

    async def api_control(request: web.Request) -> web.Response:
        try:
            session, _ = session_from_request(request, manager)
            body = await request.json()
            uid = _parse_user_id(body, session)
            manager.check_member_web(session, uid, need_control=True)
            action = str(body.get("action", "")).lower()
            msg = ""
            if action == "pause":
                msg = await session.pause()
            elif action == "resume":
                msg = await session.resume()
            elif action == "skip":
                msg = await session.skip()
            elif action == "stop":
                msg = await session.stop()
            elif action == "shuffle":
                msg = await session.shuffle_queue()
            elif action == "volume":
                msg = await session.set_volume(int(body.get("level", 100)))
            elif action == "loop":
                mode = LoopMode(str(body.get("mode", "off")))
                msg = await session.set_loop(mode)
            elif action == "move":
                msg = await session.move_track(int(body["from"]), int(body["to"]))
            else:
                return web.json_response({"ok": False, "error": f"Unknown action: {action}"}, status=400)
            return web.json_response({"ok": True, "message": msg})
        except Exception as exc:
            return _api_error_response(exc, context="api_control")

    async def api_oauth_url(request: web.Request) -> web.Response:
        session, token = session_from_request(request, manager)
        state = f"{session.session_id}:{token}"
        return web.json_response({"url": oauth_authorize_url(state)})

    async def oauth_callback(request: web.Request) -> web.Response:
        code = request.query.get("code")
        state = request.query.get("state", "")
        if not code or ":" not in state:
            return web.Response(text="Invalid OAuth callback", status=400)
        session_id, token = state.split(":", 1)
        session = manager.validate_token(session_id, token)
        if not session:
            return web.Response(text="Session expired", status=401)
        try:
            token_data = await discord_oauth_exchange(code)
            user = await discord_fetch_user(token_data["access_token"])
            user_id = int(user["id"])
            session.oauth_users[user_id] = time.time()
            manager.check_member_web(session, user_id, need_queue=True)
        except Exception as exc:
            log.exception("OAuth failed")
            return web.Response(text=f"Login failed: {exc}", status=500)

        resp = web.HTTPFound(f"/{session_id}?token={token}&logged_in=1")
        return resp

    async def ws_events(request: web.Request) -> web.WebSocketResponse:
        session, token = session_from_request(request, manager)
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async def push():
            if not ws.closed:
                await ws.send_str(json.dumps(session.state_dict()))

        session.subscribe(push)
        await push()
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        finally:
            if push in session._ws_callbacks:
                session._ws_callbacks.remove(push)
        return ws

    app.router.add_get("/health", health)
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_get("/api/session/{session_id}/state", api_state)
    app.router.add_get("/api/session/{session_id}/search", api_search)
    app.router.add_post("/api/session/{session_id}/search", api_search)
    app.router.add_post("/api/session/{session_id}/queue", api_queue_add)
    app.router.add_delete("/api/session/{session_id}/queue/{index}", api_queue_remove)
    app.router.add_post("/api/session/{session_id}/control", api_control)
    app.router.add_get("/api/session/{session_id}/oauth", api_oauth_url)
    app.router.add_get("/api/session/{session_id}/ws", ws_events)
    app.router.add_get("/static/{path}", serve_static)
    app.router.add_get("/{session_id}", serve_spa)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", _music_port())
    await site.start()
    _server = runner
    log.info("Music dashboard HTTP on 127.0.0.1:%s", _music_port())


async def stop_music_http() -> None:
    global _server
    if _server is not None:
        await _server.cleanup()
        _server = None
