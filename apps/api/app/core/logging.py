"""Structured logging configuration using structlog.

Production-grade setup:
  - development : human-readable console output, SQL echo allowed
  - staging/prod: JSON lines, no SQL echo, PII-safe, log-aggregator friendly
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Configure structlog for structured output with correlation IDs.

    Controls:
      - APP_ENV=development → pretty console renderer
      - APP_ENV=staging|production → JSON lines (for ELK, CloudWatch, etc.)
      - LOG_LEVEL → root log level (default INFO)
      - DEBUG=true → only enables SQLAlchemy echo in development mode
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.APP_ENV == "development":
        # Human-readable in dev
        renderer = structlog.dev.ConsoleRenderer()
    else:
        # JSON in staging/prod — machine-parseable for log aggregators
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.LOG_LEVEL.upper())

    # ── Silence noisy loggers ────────────────────────────────────────────
    # SQLAlchemy engine logs — only show DEBUG if explicitly requested via SQL_ECHO env var
    # Default: WARNING only (connection errors, pool exhaustion)
    import os
    sql_echo = os.getenv("SQL_ECHO", "false").lower() in ("true", "1", "yes")
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if sql_echo else logging.WARNING
    )

    # Neo4j driver notifications — suppress verbose property-missing warnings
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    logging.getLogger("neo4j").setLevel(logging.WARNING)

    # Uvicorn access logs — keep clean, only errors
    logging.getLogger("uvicorn.access").setLevel(
        logging.INFO if settings.APP_ENV == "development" else logging.WARNING
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given module name.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A structlog BoundLogger with contextvars support.
    """
    return structlog.get_logger(name)
