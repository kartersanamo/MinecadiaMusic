"""
Shared analytics event logger for Minecadia bots.
Import from bot code: sys.path insert parent of _analytics if needed.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Optional

try:
    import mysql.connector
except ImportError:
    mysql = None  # type: ignore

try:
    import pymysql
except ImportError:
    pymysql = None  # type: ignore

_tables_ready = False
_log = logging.getLogger("analytics")

TOTAL_STAT_FIELDS = frozenset(
    {
        "tickets_closed",
        "messages_sent",
        "warnings",
        "mutes",
        "temp_bans",
        "bans",
        "screenshares",
        "manual_bans",
        "blacklists",
        "revives",
        "appeals",
        "threads_locked",
        "strike_team_votes",
        "characters_sent",
        "punishment_requests",
    }
)


def _db_config() -> Optional[dict]:
    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER")
    database = os.getenv("DB_NAME") or os.getenv("DB_DATABASE")
    if not host or not user or not database:
        return None
    return {
        "host": host,
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": user,
        "password": os.getenv("DB_PASSWORD", ""),
        "database": database,
        "autocommit": True,
    }


def _connect():
    cfg = _db_config()
    if not cfg:
        return None
    if mysql is not None:
        try:
            return mysql.connector.connect(**cfg)
        except Exception as exc:
            _log.debug("Analytics mysql.connector connect failed: %s", exc)
    if pymysql is not None:
        try:
            return pymysql.connect(
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                autocommit=bool(cfg.get("autocommit", True)),
                connect_timeout=5,
            )
        except Exception as exc:
            _log.debug("Analytics pymysql connect failed: %s", exc)
    return None


def _execute(sql: str, params: tuple = ()) -> bool:
    conn = _connect()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cur.close()
        return True
    except Exception as exc:
        _log.debug("Analytics SQL failed: %s — %s", sql[:80], exc)
        return False
    finally:
        try:
            conn.close()
        except Exception as close_exc:
            _log.debug("Analytics connection close failed: %s", close_exc)


def _today() -> str:
    return date.today().isoformat()


def _now() -> int:
    return int(time.time())


def ensure_total_statistics_row(user_id: str) -> None:
    """Ensure a total_statistics row exists for this user."""
    _execute(
        "INSERT IGNORE INTO total_statistics (user_ID, updated_at) VALUES (%s, %s)",
        (str(user_id), _now()),
    )


def increment_total_stat(user_id: str, field: str, delta: int = 1) -> None:
    """Mirror staff/stat counters into all-time total_statistics (never wiped)."""
    if field not in TOTAL_STAT_FIELDS or delta == 0:
        return
    ensure_total_statistics_row(user_id)
    _execute(
        f"""UPDATE total_statistics
            SET `{field}` = GREATEST(0, CAST(`{field}` AS SIGNED) + %s),
                updated_at = %s
            WHERE user_ID = %s""",
        (int(delta), _now(), str(user_id)),
    )


def record_member_message(user_id: str, char_count: int = 0) -> None:
    """Guild-wide message rollup (all members, per day)."""
    _execute(
        """INSERT INTO analytics_member_messages_daily
           (day, user_id, message_count, character_count)
           VALUES (%s, %s, 1, %s)
           ON DUPLICATE KEY UPDATE
             message_count = message_count + 1,
             character_count = character_count + VALUES(character_count)""",
        (_today(), str(user_id), max(0, int(char_count))),
    )


def record_staff_message(
    user_id: str, channel_id: str, char_count: int = 0
) -> None:
    """#1 — Staff message in a tracked channel."""
    _execute(
        """INSERT INTO analytics_staff_messages_daily
           (day, user_id, channel_id, message_count, character_count)
           VALUES (%s, %s, %s, 1, %s)
           ON DUPLICATE KEY UPDATE
             message_count = message_count + 1,
             character_count = character_count + VALUES(character_count)""",
        (_today(), str(user_id), str(channel_id), char_count,),
    )


def record_ticket_message(channel_id: str, *, is_staff: bool) -> None:
    """#2 — Message in an open ticket channel."""
    col = "staff_messages" if is_staff else "owner_messages"
    _execute(
        f"""INSERT INTO analytics_ticket_messages_daily
            (day, channel_id, staff_messages, owner_messages)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE {col} = {col} + 1""",
        (
            _today(),
            str(channel_id),
            1 if is_staff else 0,
            0 if is_staff else 1,
        ),
    )


def record_member_event(
    event_type: str,
    user_id: str,
    *,
    invite_code: Optional[str] = None,
    account_age_days: Optional[int] = None,
) -> None:
    """#3 — join or leave."""
    if event_type not in ("join", "leave"):
        return
    _execute(
        """INSERT INTO analytics_member_events
           (event_type, user_id, invite_code, account_age_days, created_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (
            event_type,
            str(user_id),
            invite_code,
            account_age_days,
            _now(),
        ),
    )


def record_voice_seconds(
    user_id: str, channel_id: str, seconds: int
) -> None:
    """#4 — Voice time rollup."""
    if seconds <= 0:
        return
    _execute(
        """INSERT INTO analytics_voice_daily (day, user_id, channel_id, seconds)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE seconds = seconds + VALUES(seconds)""",
        (_today(), str(user_id), str(channel_id), int(seconds)),
    )


def record_command(bot_id: str, command_name: str) -> None:
    """#5 — Slash command invocation."""
    name = (command_name or "unknown")[:128]
    _execute(
        """INSERT INTO analytics_command_daily
           (day, command_name, bot_id, invocations)
           VALUES (%s, %s, %s, 1)
           ON DUPLICATE KEY UPDATE invocations = invocations + 1""",
        (_today(), name, str(bot_id)),
    )


def record_mod_action(
    action_type: str,
    actor_id: str,
    target_id: str,
    *,
    reason: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    channel_id: Optional[str] = None,
) -> None:
    """#6 — Moderation action."""
    _execute(
        """INSERT INTO analytics_mod_actions
           (action_type, actor_id, target_id, reason, duration_seconds, channel_id, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            action_type[:32],
            str(actor_id),
            str(target_id),
            (reason or "")[:512] or None,
            duration_seconds,
            str(channel_id) if channel_id else None,
            _now(),
        ),
    )


def record_poll_vote(
    poll_message_id: str, user_id: str, option_index: int
) -> None:
    """#8 — Poll vote (upsert)."""
    _execute(
        """INSERT INTO analytics_poll_votes
           (poll_message_id, user_id, option_index, voted_at)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE option_index = VALUES(option_index),
             voted_at = VALUES(voted_at)""",
        (str(poll_message_id), str(user_id), int(option_index), _now()),
    )


def record_game_outcome(
    game_name: str,
    outcome: str,
    *,
    user_id: Optional[str] = None,
    game_id: Optional[int] = None,
    duration_seconds: Optional[int] = None,
) -> None:
    """#9 — Game session end."""
    if outcome not in ("won", "lost", "abandoned", "draw", "finished"):
        outcome = "finished"
    _execute(
        """INSERT INTO analytics_game_outcomes
           (game_name, user_id, game_id, outcome, duration_seconds, ended_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            game_name[:64],
            str(user_id) if user_id else None,
            game_id,
            outcome,
            duration_seconds,
            _now(),
        ),
    )


def record_online_sample(member_count: int, online_count: int) -> None:
    """Hourly online sample for peak-hours charts."""
    _execute(
        """INSERT INTO analytics_online_samples
           (recorded_at, member_count, online_count)
           VALUES (%s, %s, %s)""",
        (_now(), int(member_count), int(online_count)),
    )


def record_server_snapshot(
    member_count: int,
    online_count: int,
    boost_tier: int,
    boost_count: int,
) -> None:
    """#10 — Daily guild snapshot."""
    _execute(
        """INSERT INTO analytics_server_snapshots
           (day, member_count, online_count, boost_tier, boost_count, recorded_at)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
             member_count = VALUES(member_count),
             online_count = VALUES(online_count),
             boost_tier = VALUES(boost_tier),
             boost_count = VALUES(boost_count),
             recorded_at = VALUES(recorded_at)""",
        (
            _today(),
            int(member_count),
            int(online_count),
            int(boost_tier),
            int(boost_count),
            _now(),
        ),
    )


def patch_blacklist_created_at(user_id: str, created_at: Optional[int] = None) -> None:
    """#7 — Set created_at on new blacklist rows."""
    ts = created_at or _now()
    _execute(
        "UPDATE blacklists SET created_at = %s WHERE user_id = %s AND (created_at IS NULL OR created_at = 0)",
        (ts, str(user_id)),
    )


def patch_poll_created_at(message_id: str, created_at: Optional[int] = None) -> None:
    ts = created_at or _now()
    _execute(
        "UPDATE polls SET created_at = %s WHERE message_id = %s AND (created_at IS NULL OR created_at = 0)",
        (ts, str(message_id)),
    )
