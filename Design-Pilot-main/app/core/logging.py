"""
Structured logging using structlog.

All logs are JSON in production, pretty-printed in development.
Never log: passwords, API keys, full design data, user-uploaded content.
Log: user IDs, action names, timestamps, error types, request IDs.
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    """Configure structlog + stdlib logging. Call once at app startup."""
    settings = get_settings()

    # Stdlib logging at the level specified
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    )

    # Silence noisy third parties in dev
    for noisy in ("httpx", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.is_production:
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a bound structlog logger."""
    return structlog.get_logger(name)
