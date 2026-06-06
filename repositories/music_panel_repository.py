from __future__ import annotations

import json
from typing import Any

from core.database import DatabasePool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS `music_panels` (
    `guild_id` BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    `channel_id` BIGINT UNSIGNED NOT NULL,
    `message_id` BIGINT UNSIGNED NOT NULL,
    `owner_id` BIGINT UNSIGNED NOT NULL,
    `session_id` VARCHAR(36) NOT NULL,
    `session_token` VARCHAR(128) NOT NULL,
    `panel_creator_id` BIGINT UNSIGNED NULL,
    `loop_mode` VARCHAR(16) NOT NULL DEFAULT 'off',
    `activity_log` JSON NULL,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class MusicPanelRepository:
    def __init__(self, db: DatabasePool | None = None):
        self._db = db or DatabasePool.get()
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        await self._db.execute(_SCHEMA)
        self._schema_ready = True

    async def upsert(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        owner_id: int,
        session_id: str,
        session_token: str,
        panel_creator_id: int | None,
        loop_mode: str,
        activity_log: list[dict[str, Any]],
    ) -> None:
        await self.ensure_schema()
        activity_json = json.dumps(activity_log) if activity_log else None
        await self._db.execute(
            """
            INSERT INTO `music_panels` (
                `guild_id`, `channel_id`, `message_id`, `owner_id`,
                `session_id`, `session_token`, `panel_creator_id`,
                `loop_mode`, `activity_log`
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `channel_id` = VALUES(`channel_id`),
                `message_id` = VALUES(`message_id`),
                `owner_id` = VALUES(`owner_id`),
                `session_id` = VALUES(`session_id`),
                `session_token` = VALUES(`session_token`),
                `panel_creator_id` = VALUES(`panel_creator_id`),
                `loop_mode` = VALUES(`loop_mode`),
                `activity_log` = VALUES(`activity_log`)
            """,
            (
                guild_id,
                channel_id,
                message_id,
                owner_id,
                session_id,
                session_token,
                panel_creator_id,
                loop_mode,
                activity_json,
            ),
        )

    async def delete(self, guild_id: int) -> None:
        await self.ensure_schema()
        await self._db.execute(
            "DELETE FROM `music_panels` WHERE `guild_id` = %s",
            (guild_id,),
        )

    async def fetch_all(self) -> list[dict[str, Any]]:
        await self.ensure_schema()
        rows = await self._db.execute("SELECT * FROM `music_panels`")
        for row in rows:
            raw = row.get("activity_log")
            if isinstance(raw, str):
                try:
                    row["activity_log"] = json.loads(raw)
                except json.JSONDecodeError:
                    row["activity_log"] = []
            elif raw is None:
                row["activity_log"] = []
        return rows
