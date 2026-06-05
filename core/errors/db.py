"""Database error helpers."""
from __future__ import annotations

import logging
from typing import TypeVar

from core.errors.exceptions import ExternalServiceError

T = TypeVar("T")

try:
    import aiomysql
except ImportError:
    aiomysql = None  # type: ignore


def is_db_operational_error(exc: BaseException) -> bool:
    if aiomysql and isinstance(exc, aiomysql.OperationalError):
        return True
    name = type(exc).__name__.lower()
    return "operational" in name or "database" in name or "mysql" in name


def raise_if_db_unavailable(exc: BaseException) -> None:
    if is_db_operational_error(exc):
        raise ExternalServiceError(log_message=str(exc)) from exc


def log_db_failure(logger: logging.Logger, exc: BaseException, *, query_hint: str = "") -> None:
    from core.errors.logging import log_exception

    log_exception(
        logger,
        exc,
        component="database",
        extra={"query": query_hint[:200]} if query_hint else None,
    )


def log_query_failure(logger: logging.Logger, exc: BaseException, query: str) -> None:
    """Log failed SQL from per-bot core/database.py helpers."""
    log_db_failure(logger, exc, query_hint=query)
