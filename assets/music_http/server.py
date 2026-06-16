from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web


from assets.music_http.artwork_proxy import artwork_handler
from assets.music_http.auth import (
    activity_bootstrap_for_user,
    discord_fetch_user,
    discord_oauth_exchange,
    discord_oauth_exchange_activity,
    oauth_authorize_url,
    session_from_request,
)
from core.action_log import log_action
from core.errors.exceptions import UserFacingError
from core.loggers import log_http
from services.music.queue import LoopMode
from services.music.resolver import search_media

if TYPE_CHECKING:
    from discord.ext import commands

log = logging.getLogger("music_http")
_server: web.AppRunner | None = None
_WEB_DIR = Path(__file__).resolve().parent.parent / "music_web"


def _asset_version() -> str:
    version = 0
    for name in ("styles.css", "app.js", "index.html", "discord-embedded-app-sdk.mjs"):
        path = _WEB_DIR / name
        if path.is_file():
            version = max(version, int(path.stat().st_mtime))
    return str(version or 1)


def _music_port() -> int:
    return int(os.getenv("MUSIC_HTTP_PORT", "8790"))


_FRAME_ANCESTORS = (
    "frame-ancestors https://*.discord.com https://discord.com "
    "https://*.discordsays.com;"
)


@web.middleware
async def request_logging_middleware(request: web.Request, handler):
    started = time.perf_counter()
    session_id = request.match_info.get("session_id")
    path = request.path
    skip = path in ("/health",) or path.endswith("/state")
    if not skip:
        log_action(
            log_http,
            "http.request",
            level=logging.DEBUG,
            method=request.method,
            path=path,
            session_id=session_id[:8] if session_id else None,
            remote=request.remote,
        )
    try:
        response = await handler(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log_http.exception(
            "http.error method=%s path=%s elapsed_ms=%s",
            request.method,
            path,
            elapsed_ms,
        )
        raise
    if not skip:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        status = response.status if isinstance(response, web.Response) else 200
        log_action(
            log_http,
            "http.response",
            level=logging.DEBUG,
            method=request.method,
            path=path,
            status=status,
            elapsed_ms=elapsed_ms,
            session_id=session_id[:8] if session_id else None,
        )
    return response


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response = await handler(request)
    if isinstance(response, web.Response):
        response.headers.setdefault("Content-Security-Policy", _FRAME_ANCESTORS)
    return response


def _discord_client_id() -> str:
    return os.getenv("DISCORD_CLIENT_ID", "")


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
    if session.panel_creator_id:
        return int(session.panel_creator_id)
    if session.oauth_users:
        return int(next(iter(session.oauth_users.keys())))
    raise web.HTTPUnauthorized(
        text='{"error":"Discord login required"}',
        content_type="application/json",
    )


async def start_music_http(bot: "commands.Bot") -> None:
    global _server
    manager = bot.app.music
    app = web.Application(middlewares=[request_logging_middleware, security_headers_middleware])

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "lavalink": manager._lavalink_ready})

    async def serve_static(request: web.Request) -> web.Response:
        name = request.match_info.get("path", "styles.css")
        path = (_WEB_DIR / name).resolve()
        if not str(path).startswith(str(_WEB_DIR.resolve())):
            raise web.HTTPForbidden()
        if not path.is_file():
            raise web.HTTPNotFound()
        ctype = "text/css"
        if path.suffix in (".js", ".mjs"):
            ctype = "application/javascript"
        return web.Response(
            body=path.read_bytes(),
            content_type=ctype,
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def serve_spa(request: web.Request) -> web.Response:
        index = _WEB_DIR / "index.html"
        if not index.is_file():
            raise web.HTTPNotFound()
        version = _asset_version()
        css_path = _WEB_DIR / "styles.css"
        js_path = _WEB_DIR / "app.js"
        html = index.read_text(encoding="utf-8")
        css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
        js = js_path.read_text(encoding="utf-8") if js_path.is_file() else ""
        js = js.replace("</script>", "<\\/script>")
        html = html.replace("__ASSET_VERSION__", version)
        html = html.replace("__DISCORD_CLIENT_ID__", _discord_client_id())
        html = html.replace(
            "__INLINE_STYLES__",
            f'<style id="inlined-styles">{css}</style>',
        )
        html = html.replace(
            "__BOOT_SCRIPTS__",
            (
                '<script type="module">\n'
                f'import {{ DiscordSDK }} from "/static/discord-embedded-app-sdk.mjs?v={version}";\n'
                "window.__DiscordSDK = DiscordSDK;\n"
                "window.dispatchEvent(new Event('discord-sdk-ready'));\n"
                "</script>\n"
                f'<script id="jsScript">\n{js}\n</script>'
            ),
        )
        return web.Response(
            text=html,
            content_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "CDN-Cache-Control": "no-store",
                "Surrogate-Control": "no-store",
                "X-Accel-Expires": "0",
            },
        )

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
        results = await search_media(q, limit=10)
        return web.json_response({"results": [r.to_dict() for r in results]})

    async def api_queue_add(request: web.Request) -> web.Response:
        try:
            session, _ = session_from_request(request, manager)
            body = await request.json()
            uid = _parse_user_id(body, session)
            member = manager.check_member_web(session, uid, need_queue=True)
            query = body.get("query") or body.get("uri")
            identifier = body.get("identifier")
            kind = str(body.get("kind", "track")).lower()
            playlist_title = body.get("playlistTitle") or body.get("playlist_title")
            log_action(
                log_http,
                "api.queue_add",
                guild_id=session.guild_id,
                user_id=uid,
                kind=kind,
                query=(str(query)[:120] if query else None),
                identifier=(str(identifier)[:120] if identifier else None),
            )
            if not identifier and not query:
                return web.json_response({"ok": False, "error": "query or identifier required"}, status=400)
            from services.music.resolver import resolve_query, tracks_from_identifier

            if identifier:
                tracks = await tracks_from_identifier(str(identifier), kind=kind)
            else:
                result = await resolve_query(str(query))
                if isinstance(result, list):
                    tracks = result[:1]
                else:
                    tracks = list(result.tracks)
                    kind = "playlist"
                    playlist_title = playlist_title or result.name
            for t in tracks:
                t.extras = {"requester_id": member.id}
            count, started = await session.add_tracks(
                tracks,
                member.id,
                connect_member=member,
                playlist_title=playlist_title if kind == "playlist" else None,
            )
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
            msg = await session.remove_at(index, actor_id=uid)
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
            log_action(
                log_http,
                "api.control",
                guild_id=session.guild_id,
                user_id=uid,
                control=action,
            )
            msg = ""
            if action == "pause":
                msg = await session.pause(actor_id=uid)
            elif action == "resume":
                msg = await session.resume(actor_id=uid)
            elif action == "skip":
                msg = await session.skip(actor_id=uid)
            elif action == "stop":
                msg = await session.stop(actor_id=uid)
            elif action == "shuffle":
                msg = await session.shuffle_queue(actor_id=uid)
            elif action == "volume":
                msg = await session.set_volume(int(body.get("level", 100)), actor_id=uid)
            elif action == "loop":
                mode = LoopMode(str(body.get("mode", "off")))
                msg = await session.set_loop(mode, actor_id=uid)
            elif action == "move":
                msg = await session.move_track(
                    int(body["from"]),
                    int(body["to"]),
                    actor_id=uid,
                )
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

    async def api_activity_token(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            code = str(body.get("code", "")).strip()
            if not code:
                return web.json_response({"ok": False, "error": "Missing code"}, status=400)
            token_data = await discord_oauth_exchange_activity(code)
            return web.json_response(
                {
                    "ok": True,
                    "access_token": token_data["access_token"],
                    "token_type": token_data.get("token_type", "Bearer"),
                    "expires_in": token_data.get("expires_in"),
                }
            )
        except Exception as exc:
            return _api_error_response(exc, context="api_activity_token")

    async def api_activity_bootstrap(request: web.Request) -> web.Response:
        try:
            auth = request.headers.get("Authorization", "")
            if not auth.lower().startswith("bearer "):
                return web.json_response({"ok": False, "error": "Missing access token"}, status=401)
            access_token = auth[7:].strip()
            guild_raw = request.query.get("guild_id", "").strip()
            if not guild_raw.isdigit():
                return web.json_response({"ok": False, "error": "Missing guild_id"}, status=400)
            guild_id = int(guild_raw)
            user = await discord_fetch_user(access_token)
            user_id = int(user["id"])
            payload = await activity_bootstrap_for_user(
                manager,
                guild_id=guild_id,
                user_id=user_id,
            )
            return web.json_response({"ok": True, **payload})
        except web.HTTPException:
            raise
        except Exception as exc:
            return _api_error_response(exc, context="api_activity_bootstrap")

    async def ws_events(request: web.Request) -> web.WebSocketResponse:
        session, token = session_from_request(request, manager)
        log_action(
            log_http,
            "ws.connect",
            level=logging.DEBUG,
            guild_id=session.guild_id,
            session_id=session.session_id[:8],
        )
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
            log_action(
                log_http,
                "ws.disconnect",
                level=logging.DEBUG,
                guild_id=session.guild_id,
                session_id=session.session_id[:8],
            )
        return ws

    app.router.add_get("/health", health)
    app.router.add_get("/api/artwork", artwork_handler)
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_post("/api/activity/token", api_activity_token)
    app.router.add_get("/api/activity/bootstrap", api_activity_bootstrap)
    app.router.add_get("/api/session/{session_id}/state", api_state)
    app.router.add_get("/api/session/{session_id}/search", api_search)
    app.router.add_post("/api/session/{session_id}/search", api_search)
    app.router.add_post("/api/session/{session_id}/queue", api_queue_add)
    app.router.add_delete("/api/session/{session_id}/queue/{index}", api_queue_remove)
    app.router.add_post("/api/session/{session_id}/control", api_control)
    app.router.add_get("/api/session/{session_id}/oauth", api_oauth_url)
    app.router.add_get("/api/session/{session_id}/ws", ws_events)
    app.router.add_get("/static/{path}", serve_static)
    app.router.add_get("/", serve_spa)
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
