from discord.ext import commands

from core.config import ConfigManager
from core.database import DatabasePool
from repositories.statistics_repository import StatisticsRepository
from services.embed_service import EmbedService
from services.music.session_manager import MusicSessionManager
from services.statistics_service import StatisticsService


class BotApp:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings = ConfigManager.all()
        self.db = DatabasePool.get()
        self.statistics_repo = StatisticsRepository(self.db)
        self.statistics = StatisticsService(self.statistics_repo)
        self.embeds = EmbedService()
        self.music = MusicSessionManager(bot)

    @classmethod
    def from_bot(cls, bot: commands.Bot) -> "BotApp":
        return cls(bot)
