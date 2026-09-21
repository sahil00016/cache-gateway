"""Structured JSON logging.

Logs are JSON because they are read by machines first and humans second. Every
record carries the request id, so one request can be followed across the cache,
the database and the dispatcher without correlating by timestamp.
"""

import logging
import sys
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from src.common.settings import Settings
from src.core.context import get_request_id


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add ``request_id`` to the record.

        Args:
            record: The record being emitted.

        Returns:
            Always True; this filter enriches rather than excludes.
        """
        record.request_id = get_request_id()
        return True


class ServiceJsonFormatter(JsonFormatter):
    """JSON formatter that guarantees a stable set of top-level fields."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Populate the emitted record.

        Args:
            log_record: The dict that will be serialised to JSON.
            record: The original logging record.
            message_dict: Extra fields parsed from the log message.
        """
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["request_id"] = getattr(record, "request_id", "-")


def configure_logging(settings: Settings) -> None:
    """Install the JSON formatter on the root logger.

    Called once during application startup. Existing handlers are replaced
    rather than added to, so repeated calls in tests do not duplicate output.

    Args:
        settings: Validated application settings.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ServiceJsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s", timestamp=True)
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.value)

    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
