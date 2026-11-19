"""Structured logging (structlog). JSON in prod, key=value in dev; job_id/tenant_id are bound via contextvars."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(json: bool = False, level: str = "INFO") -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


def bind_context(**kw: Any) -> None:
    structlog.contextvars.bind_contextvars(**kw)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
