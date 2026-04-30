"""Structlog configuration for structured logging."""

import logging
import sys

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog with console output.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR).
    """
    # Standard logging config for non-structlog libraries
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.WARNING,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(*args, **kwargs):
    """Get a structlog logger instance.

    Returns:
        Configured structlog BoundLogger.
    """
    return structlog.get_logger(*args, **kwargs)
