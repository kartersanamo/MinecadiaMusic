import json
import os
from typing import Any, Optional

from dotenv import load_dotenv

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env"))

class ConfigManager:
    """Singleton access to bot configuration."""

    _instance: Optional["ConfigManager"] = None
    _extra_files: dict[str, dict] = {}
    _tickets: Optional[dict] = None

    @classmethod
    def get_instance(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        settings = cls.get_instance().settings
        if "." not in key:
            return settings.get(key, default)
        value: Any = settings
        for part in key.split("."):
            if not isinstance(value, dict):
                return default
            value = value.get(part)
            if value is None:
                return default
        return value

    @classmethod
    def all(cls) -> dict:
        return dict(cls.get_instance().settings)

    @classmethod
    def get_db_config(cls) -> dict:
        return cls.get_instance()._resolve_db_config()


    @classmethod
    def load(cls, name: str) -> dict:
        if name == "config":
            return cls.all()
        if name not in cls._extra_files:
            path = os.path.join(_project_root, f"assets/{name}.json")
            with open(path, "r") as handle:
                cls._extra_files[name] = json.load(handle)
        return cls._extra_files[name]

    def __init__(self):
        with open(os.path.join(_project_root, "assets/config.json"), "r") as file:
            data = json.load(file)
        data["TOKEN"] = os.getenv("DISCORD_TOKEN", data.get("TOKEN", ""))
        data["SYNC_AUTH"] = os.getenv("SYNC_AUTH", data.get("SYNC_AUTH", ""))
        data["SCREENSHARES_WEBHOOK"] = os.getenv(
            "SCREENSHARES_WEBHOOK", data.get("SCREENSHARES_WEBHOOK", "")
        )
        data["BUG_REPORT_WEBHOOK"] = os.getenv(
            "BUG_REPORT_WEBHOOK", data.get("BUG_REPORT_WEBHOOK", "")
        )
        if os.getenv("DB_HOST"):
            data["DATABASE_CONFIG"] = self._db_config_from_env()
        self.settings = data
    @staticmethod
    def _db_config_from_env() -> dict:
        return {
            "host": os.getenv("DB_HOST", "127.0.0.1"),
            "port": int(os.getenv("DB_PORT", "3306")),
            "user": os.getenv("DB_USER", ""),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "") or os.getenv("DB_DATABASE", ""),
            "autocommit": os.getenv("DB_AUTOCOMMIT", "true").lower() in ("1", "true", "yes"),
        }

    def _resolve_db_config(self) -> dict:
        if os.getenv("DB_HOST"):
            return self._db_config_from_env()
        return self.settings.get("DATABASE_CONFIG") or {}


__all__ = ["ConfigManager"]
