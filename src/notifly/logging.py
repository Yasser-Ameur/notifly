"""Structured logging setup.

Every log record is enriched with the active correlation ID (threaded through
the application via a ``contextvars.ContextVar``) so a single request can be
traced across the API, database, queue, worker, and providers.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

CORRELATION_ID_VAR: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return CORRELATION_ID_VAR.get()


def set_correlation_id(correlation_id: str | None) -> None:
    CORRELATION_ID_VAR.set(correlation_id)


class CorrelationIdFormatter(logging.Formatter):
    """Injects the current correlation ID into every log record."""

    def format(self, record: logging.LogRecord) -> str:
        record.correlation_id = get_correlation_id() or "-"
        return super().format(record)


class CorrelationJsonFormatter(JsonFormatter, CorrelationIdFormatter):
    pass


def configure_logging(*, level: str, json_logs: bool) -> None:
    logger = logging.getLogger()
    logger.setLevel(level.upper())
    logger.handlers.clear()

    if json_logs:
        formatter: logging.Formatter = CorrelationJsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(correlation_id)s %(message)s",
            rename_fields={"correlation_id": "correlation_id"},
        )
    else:
        formatter = CorrelationIdFormatter(
            "%(asctime)s %(levelname)s %(correlation_id)s %(name)s: %(message)s"
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
