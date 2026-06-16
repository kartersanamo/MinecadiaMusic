from typing import Optional

import aiomysql

from core.config import ConfigManager
from core.loggers import log_database
from core.action_log import log_action


class DatabasePool:
    _instance: Optional["DatabasePool"] = None

    @classmethod
    def get(cls) -> "DatabasePool":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(self):
        cfg = ConfigManager.get_db_config()
        return await aiomysql.connect(
            host=cfg.get("host", "127.0.0.1"),
            port=cfg.get("port", 3306),
            user=cfg.get("user", ""),
            password=cfg.get("password", ""),
            db=cfg.get("database", ""),
            autocommit=bool(cfg.get("autocommit", True)),
            cursorclass=aiomysql.DictCursor,
        )

    async def execute(self, query: str, params: tuple | None = None) -> list:
        rows = []
        connection = None
        preview = " ".join(query.split())[:160]
        log_database.debug("db.execute query=%s params=%s", preview, params)
        try:
            connection = await self.connect()
            async with connection.cursor() as cursor:
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                rows = await cursor.fetchall()
            log_action(log_database, "db.execute.ok", rows=len(rows))
        except Exception as error:
            from core.errors.db import log_query_failure

            log_query_failure(log_database, error, query)
        finally:
            if connection:
                connection.close()
        return rows


async def execute(query: str, params: tuple | None = None) -> list:
    return await DatabasePool.get().execute(query, params)
