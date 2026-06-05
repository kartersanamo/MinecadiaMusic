from typing import Optional

import aiomysql

from core.config import ConfigManager
from core.loggers import log_tasks


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
        try:
            connection = await self.connect()
            async with connection.cursor() as cursor:
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                rows = await cursor.fetchall()
        except Exception as error:
            from core.errors.db import log_query_failure

            log_query_failure(log_tasks, error, query)
        finally:
            if connection:
                connection.close()
        return rows


async def execute(query: str, params: tuple | None = None) -> list:
    return await DatabasePool.get().execute(query, params)
