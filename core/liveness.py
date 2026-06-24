"""Process and Discord connection liveness monitoring.

A frozen asyncio event loop leaves the process alive with no logs and a bot that
appears offline. In-process asyncio tasks cannot recover from that. A background
thread periodically pings the event loop via call_soon_threadsafe; if the loop
does not respond, the process is killed so run.sh can restart it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Optional

import discord
from discord.ext import commands

LOOP_PING_INTERVAL = 45
LOOP_PING_TIMEOUT = 20
DISCONNECT_RESTART_THRESHOLD = 300
GATEWAY_CHECK_INTERVAL = 90
GATEWAY_FAILURE_THRESHOLD = 2
GATEWAY_LATENCY_LIMIT = 120.0

_last_heartbeat = time.monotonic()
_heartbeat_lock = threading.Lock()
_watchdog_started = False
_disconnected_at: Optional[float] = None
_gateway_failures = 0
_liveness_task: Optional[asyncio.Task] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def touch_heartbeat() -> None:
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.monotonic()


def stale_seconds() -> float:
    with _heartbeat_lock:
        return time.monotonic() - _last_heartbeat


def mark_disconnected() -> None:
    global _disconnected_at
    if _disconnected_at is None:
        _disconnected_at = time.time()


def mark_connected() -> None:
    global _disconnected_at, _gateway_failures
    _disconnected_at = None
    _gateway_failures = 0


def _force_restart(log: logging.Logger, bot_name: str, reason: str) -> None:
    log.critical("[%s] %s — exiting for restart", bot_name, reason)
    os._exit(1)


def _ping_event_loop(loop: asyncio.AbstractEventLoop) -> bool:
    """Return True if the event loop processed a callback within LOOP_PING_TIMEOUT."""
    event = threading.Event()
    ping_ok = False

    def _ping() -> None:
        nonlocal ping_ok
        touch_heartbeat()
        ping_ok = True
        event.set()

    try:
        loop.call_soon_threadsafe(_ping)
    except RuntimeError:
        return False

    return event.wait(timeout=LOOP_PING_TIMEOUT) and ping_ok


def _watchdog_thread(loop: asyncio.AbstractEventLoop, log: logging.Logger, bot_name: str) -> None:
    while True:
        time.sleep(LOOP_PING_INTERVAL)

        if loop.is_closed():
            _force_restart(log, bot_name, "Event loop closed unexpectedly")
            return

        if not _ping_event_loop(loop):
            _force_restart(
                log,
                bot_name,
                f"Event loop unresponsive for {LOOP_PING_TIMEOUT}s",
            )
            return


def _start_thread_watchdog(
    loop: asyncio.AbstractEventLoop,
    log: logging.Logger,
    bot_name: str,
) -> None:
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    touch_heartbeat()
    thread = threading.Thread(
        target=_watchdog_thread,
        args=(loop, log, bot_name),
        name=f"{bot_name}-watchdog",
        daemon=True,
    )
    thread.start()
    log.info(
        "[%s] Event loop watchdog started (ping every %ss, timeout %ss)",
        bot_name,
        LOOP_PING_INTERVAL,
        LOOP_PING_TIMEOUT,
    )


def _websocket_healthy(bot: commands.Bot) -> None:
    ws = getattr(bot, "ws", None)
    if ws is None:
        raise RuntimeError("No gateway websocket")
    if getattr(ws, "closed", False):
        raise RuntimeError("Gateway websocket is closed")

    latency = bot.latency
    if latency == float("inf") or latency > GATEWAY_LATENCY_LIMIT:
        raise RuntimeError(f"Gateway latency unhealthy: {latency}")


def _resolve_probe_activity(bot: commands.Bot) -> discord.BaseActivity | None:
    """Resolve configured activity for gateway probes; never clear presence with None."""
    stored = getattr(bot, "_presence_activity", None)
    if isinstance(stored, discord.BaseActivity):
        return stored
    if bot.activity and isinstance(bot.activity, discord.BaseActivity):
        return bot.activity
    for guild in bot.guilds:
        me = guild.me
        if me is not None and me.activities:
            return me.activities[0]
    return None


async def _probe_discord(bot: commands.Bot) -> None:
    if not bot.user:
        raise RuntimeError("Bot user not available")

    _websocket_healthy(bot)

    # Cached guild lookups do not prove connectivity — always round-trip REST.
    await asyncio.wait_for(bot.fetch_user(bot.user.id), timeout=15.0)

    # Presence updates go through the gateway; catches zombie REST-only states.
    # change_presence(activity=None) clears the status — only probe when we have one.
    activity = _resolve_probe_activity(bot)
    if activity is not None:
        await asyncio.wait_for(bot.change_presence(activity=activity), timeout=15.0)


async def _verify_gateway(bot: commands.Bot, log: logging.Logger, bot_name: str) -> None:
    global _gateway_failures

    if not bot.is_ready():
        return

    try:
        await _probe_discord(bot)
        _gateway_failures = 0
    except Exception as exc:
        _gateway_failures += 1
        log.warning(
            "[%s] Discord health check failed (%s/%s): %s",
            bot_name,
            _gateway_failures,
            GATEWAY_FAILURE_THRESHOLD,
            exc,
        )
        if _gateway_failures >= GATEWAY_FAILURE_THRESHOLD:
            _force_restart(
                log,
                bot_name,
                f"Discord health check failed {_gateway_failures} times",
            )


async def _liveness_loop(bot: commands.Bot, log: logging.Logger, bot_name: str) -> None:
    global _disconnected_at
    last_gateway_check = 0.0

    while not bot.is_closed():
        touch_heartbeat()

        if bot.is_ready():
            _disconnected_at = None

            now = time.monotonic()
            if now - last_gateway_check >= GATEWAY_CHECK_INTERVAL:
                last_gateway_check = now
                await _verify_gateway(bot, log, bot_name)
        else:
            if _disconnected_at is None:
                _disconnected_at = time.time()
                log.warning("[%s] Bot not ready — monitoring for reconnect", bot_name)
            elif time.time() - _disconnected_at > DISCONNECT_RESTART_THRESHOLD:
                _force_restart(
                    log,
                    bot_name,
                    f"Not ready for {time.time() - _disconnected_at:.0f}s without reconnect",
                )

        await asyncio.sleep(30)


async def start_liveness_monitor(
    bot: commands.Bot,
    *,
    log: logging.Logger,
    bot_name: str,
) -> None:
    """Start thread watchdog and asyncio Discord health checks."""
    global _liveness_task, _loop

    _loop = asyncio.get_running_loop()
    _start_thread_watchdog(_loop, log, bot_name)

    if _liveness_task is not None and not _liveness_task.done():
        return

    _liveness_task = asyncio.create_task(_liveness_loop(bot, log, bot_name))
