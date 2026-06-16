import functools
import time

from core.errors.logging import log_exception
from core.loggers import log_tasks


def task(action_name: str, log: bool = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            log_tasks.info("%s started", action_name)
            try:
                result = await func(*args, **kwargs)
                time_elapsed = round((time.perf_counter() - start_time), 2)
                if time_elapsed > 3:
                    log_tasks.warning(
                        "%s finished in %ss (slow)",
                        action_name,
                        time_elapsed,
                    )
                else:
                    log_tasks.info("%s finished in %ss", action_name, time_elapsed)
                return result
            except Exception as error:
                log_exception(
                    log_tasks,
                    error,
                    bot_name="Utilities",
                    component=action_name,
                    extra={"elapsed_s": round((time.perf_counter() - start_time), 2)},
                )
                raise error

        return wrapper

    return decorator
