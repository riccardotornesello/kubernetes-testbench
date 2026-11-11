"""
Structured logging for the testbench, built on the standard `logging` module.

Usage:

    from core.log import get_logger

    log = get_logger(__name__)
    log.info("Installing Liqo", cluster="a", version="v1.3.0")

    # Context bound once and attached to every record
    cluster_log = log.bind(cluster="a")
    cluster_log.warning("Attempt failed", attempt=2)

Keyword arguments become structured fields: rendered as `key=value` pairs by
the console format, or as JSON keys by the json format (one object per line).
The usual `exc_info`, `stack_info` and `stacklevel` arguments still work.

`setup_logging` must be called once, at startup (see main.py).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from typing import Any, Literal

LogFormat = Literal["console", "json"]

# Attribute of the LogRecord holding the structured fields
FIELDS_ATTR = "fields"

# Arguments of Logger._log that are not structured fields
_LOGGING_KWARGS = ("exc_info", "stack_info", "stacklevel", "extra")

# Third-party libraries whose DEBUG/INFO logs are noise for the testbench
_NOISY_LOGGERS = ("urllib3", "kubernetes", "docker")


class StructuredLogger(logging.LoggerAdapter):
    """Logger accepting structured fields as keyword arguments."""

    def __init__(
        self, logger: logging.Logger, context: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(logger, dict(context or {}))

    def bind(self, **fields: Any) -> StructuredLogger:
        """Returns a logger attaching `fields` (and the bound ones) to every record."""
        return StructuredLogger(self.logger, {**self.extra, **fields})

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        """Moves the keyword arguments into the structured fields."""
        fields = {
            key: kwargs.pop(key) for key in list(kwargs) if key not in _LOGGING_KWARGS
        }
        extra = dict(kwargs.get("extra") or {})
        extra[FIELDS_ATTR] = {**self.extra, **fields}
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str) -> StructuredLogger:
    """Structured logger named `name`."""
    return StructuredLogger(logging.getLogger(name))


def _record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Structured fields of a record."""
    return getattr(record, FIELDS_ATTR, None) or {}


def _timestamp(record: logging.LogRecord) -> str:
    """UTC ISO 8601 timestamp of a record."""
    return (
        datetime.fromtimestamp(record.created, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ConsoleFormatter(logging.Formatter):
    """`<time> <LEVEL> <logger> <message> key=value ...`, colored on a TTY."""

    _COLORS = {
        logging.DEBUG: "\033[2m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    _RESET = "\033[0m"
    _DIM = "\033[2m"

    def __init__(self, color: bool) -> None:
        super().__init__()
        self.color = color

    @staticmethod
    def _format_value(value: Any) -> str:
        """Quotes the values containing spaces, quotes or '='."""
        text = str(value)
        if text == "" or any(c.isspace() or c in '"=' for c in text):
            return json.dumps(text)
        return text

    def format(self, record: logging.LogRecord) -> str:
        level = f"{record.levelname:<8}"
        name = record.name
        fields = " ".join(
            f"{key}={self._format_value(value)}"
            for key, value in _record_fields(record).items()
        )
        if self.color:
            level = f"{self._COLORS.get(record.levelno, '')}{level}{self._RESET}"
            name = f"{self._DIM}{name}{self._RESET}"
            if fields:
                fields = f"{self._DIM}{fields}{self._RESET}"

        line = f"{_timestamp(record)} {level} {name}: {record.getMessage()}"
        if fields:
            line = f"{line}  {fields}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per line, structured fields as top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": _timestamp(record),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in _record_fields(record).items():
            # Never let a field shadow the base keys
            payload[key if key not in payload else f"field.{key}"] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str | int = "INFO", fmt: LogFormat = "console") -> None:
    """
    Configures the root logger: logs go to stderr (stdout is left to the
    output of the external commands, shown at DEBUG level, see
    utils.commands), in the given format.
    """
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter(color=sys.stderr.isatty()))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else level.upper())

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
