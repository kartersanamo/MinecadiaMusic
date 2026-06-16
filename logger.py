from logging.handlers import TimedRotatingFileHandler
import logging.config
import datetime
import os
import pytz

GRAY = "\033[90m"
LIGHT_PINK = "\033[95m"
RESET = "\033[0m"

EST = pytz.timezone('US/Eastern')
os.makedirs("logs", exist_ok=True)
current_time_est = datetime.datetime.now(EST)
log_filename = f"logs/{current_time_est.strftime('%Y-%m-%d')}.log"

_APP_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Third-party loggers — keep quiet unless something is wrong.
_QUIET_LOGGERS = (
    "discord",
    "discord.client",
    "discord.gateway",
    "discord.http",
    "discord.webhook",
    "discord.state",
    "discord.voice",
    "wavelink",
    "aiohttp",
    "aiohttp.access",
    "aiohttp.client",
    "aiohttp.server",
    "aiohttp.web",
    "urllib3",
    "asyncio",
)

# App loggers — meaningful actions only at INFO by default.
_APP_LOGGERS = (
    "Tasks",
    "Commands",
    "Music",
    "music_http",
    "UI",
    "Events",
    "Database",
)


class ESTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt = None):
        dt = datetime.datetime.fromtimestamp(record.created, EST)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            s = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        return s

class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, filename, when='midnight', interval=1, backupCount=7, encoding=None, delay=False, utc=False, atTime=None):
        super().__init__(filename, when, interval, backupCount, encoding, delay, utc, atTime)
        if not hasattr(self, 'suffix'):
            self.suffix = "%Y-%m-%d"

    def doRollover(self):
        self.stream.close()
        current_time = int(self.rolloverAt - self.interval)
        dfn = self.baseFilename + "." + datetime.datetime.utcfromtimestamp(current_time).strftime(self.suffix)
        if not os.path.exists(dfn) and os.path.exists(self.baseFilename):
            os.rename(self.baseFilename, dfn)
        if self.backupCount > 0:
            for s in self.getFilesToDelete():
                os.remove(s)
        self.mode = 'w'
        self.stream = self._open()
        self.rolloverAt = self.rolloverAt + self.interval

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "file": {
            "format": "%(levelname)-10s  %(asctime)s  %(name)-14s  %(funcName)-20s  %(message)s",
            "()": ESTFormatter
        },
        "standard": {
            "format": f"{GRAY}%(asctime)s{RESET} %(levelname)-8s {LIGHT_PINK}%(name)s{RESET} %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "()": ESTFormatter
        }
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "standard"
        },
        "file": {
            "level": "DEBUG",
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": log_filename,
            "when": "midnight",
            "interval": 1,
            "backupCount": 14,
            "formatter": "file"
        }
    },
    "loggers": {
        **{
            name: {
                "handlers": ["console", "file"],
                "level": _APP_LEVEL,
                "propagate": False,
            }
            for name in _APP_LOGGERS
        },
        **{
            name: {
                "handlers": ["console", "file"],
                "level": "WARNING",
                "propagate": False,
            }
            for name in _QUIET_LOGGERS
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "WARNING",
    },
}

logging.config.dictConfig(LOGGING_CONFIG)

for logger_name in (*_APP_LOGGERS, *_QUIET_LOGGERS):
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler):
            logger.removeHandler(handler)
            new_handler = CustomTimedRotatingFileHandler(
                handler.baseFilename,
                when=handler.when,
                interval=handler.interval,
                backupCount=handler.backupCount,
                encoding=handler.encoding,
                delay=handler.delay,
                utc=handler.utc,
                atTime=handler.atTime
            )
            new_handler.setFormatter(handler.formatter)
            logger.addHandler(new_handler)

logging.getLogger("Tasks").info(
    "Logging initialized app_level=%s file=%s",
    _APP_LEVEL,
    log_filename,
)
