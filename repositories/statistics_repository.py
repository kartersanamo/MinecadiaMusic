
from core.database import DatabasePool


class StatisticsRepository:
    def __init__(self, db: DatabasePool | None = None):
        self._db = db or DatabasePool.get()

    async def find_row(self, user_id: int) -> list:
        return await self._db.execute(
            "SELECT * FROM `staff_statistics` WHERE `user_id` = %s",
            (user_id,),
        )

    async def insert_default_row(self, user_id: int) -> None:
        await self._db.execute(
            "INSERT INTO `staff_statistics` (`user_id`, `tickets_closed`, `messages_sent`, `warnings`, "
            "`mutes`, `temp_bans`, `bans`, `screenshares`, `manual_bans`, `blacklists`, `revives`, "
            "`appeals`, `threads_locked`, `strike_team_votes`, `characters_sent`, `punishment_requests`) "
            "VALUES (%s, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
            (user_id,),
        )

    async def increment_statistic(self, user_id: int, column: str, value: int) -> None:
        await self._db.execute(
            f"UPDATE `staff_statistics` SET `{column}` = %s WHERE `user_id` = %s",
            (value, user_id),
        )
