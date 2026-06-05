import discord

from repositories.statistics_repository import StatisticsRepository


class StatisticsService:
    def __init__(self, repository: StatisticsRepository | None = None):
        self._repo = repository or StatisticsRepository()

    async def get_statistic(self, user: discord.Member, statistic: str):
        rows = await self._repo.find_row(user.id)
        if rows:
            return rows[0][statistic]
        await self._repo.insert_default_row(user.id)
        return 0

    async def increment_statistic(self, user: discord.Member, statistic: str) -> None:
        current = await self.get_statistic(user, statistic)
        await self._repo.increment_statistic(user.id, statistic, current + 1)
