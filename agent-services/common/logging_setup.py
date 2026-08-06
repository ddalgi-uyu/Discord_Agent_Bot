"""Structured logging setup for agent-services.

Leaf module — must not import from any other project module.

Two entry points are provided:

* :func:`setup_logging` — one-shot configuration of the root logger with a
  console handler and (optionally) a rotating file handler. Idempotent: safe
  to call multiple times.
* :func:`get_logger` — returns a child logger prefixed with the configured
  ``app_name``.

The JSON formatter uses only the stdlib :mod:`json` module so no extra
dependency is required.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Rotating file handler defaults — kept module-level so tests can introspect.
_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT: int = 3

# Standard set of JSON fields emitted on every record.
_RESERVED_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Format :class:`logging.LogRecord` instances as single-line JSON.

    Always emits ``timestamp`` (ISO-8601 UTC), ``level``, ``name`` and
    ``message`` fields. Any extra attributes attached to the record are also
    serialised, which makes structured context easy to add via
    ``logger.info("msg", extra={"key": "value"})``.
    """

    def format(self, record: logging.LogRecord) -> str:
        # ISO-8601 UTC timestamp with millisecond precision.
        timestamp = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # Surface any extras attached via ``extra={...}``.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value

        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # Last-resort fallback: emit a minimal safe payload.
            safe = {
                "timestamp": timestamp,
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            }
            return json.dumps(safe, ensure_ascii=False)


_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_CONSOLE_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"

# Name of the attribute on the root logger that caches the configured app_name
# so ``get_logger`` can prefix child loggers consistently across calls.
_APP_NAME_ATTR = "_app_name"


def _resolve_level(level: str | int) -> int:
    """Coerce ``level`` to a :mod:`logging` numeric level.

    Accepts case-insensitive names (``"INFO"``, ``"info"``) or numeric values.
    Unknown names default to :data:`logging.INFO`.
    """
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        coerced = logging.getLevelName(level.upper())
        if isinstance(coerced, int):
            return coerced
    return logging.INFO


def setup_logging(
    level: str = "INFO",
    *,
    json: bool = False,
    log_file: Path | None = None,
    app_name: str = "app",
) -> None:
    """Configure the root logger.

    The function is idempotent: calling it multiple times replaces the
    handlers on the root logger rather than stacking duplicates.

    Parameters
    ----------
    level:
        Log level as a string (``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ...)
        or numeric value.
    json:
        When ``True`` the console handler emits JSON lines; otherwise a
        human-readable format is used.
    log_file:
        Optional path to a log file. When supplied, a
        :class:`~logging.handlers.RotatingFileHandler` is attached with a
        10 MB rotation size and 3 backups.
    app_name:
        Logical application name. Stored on the root logger so
        :func:`get_logger` can produce consistently prefixed child loggers.
    """
    numeric_level = _resolve_level(level)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Clear previously installed handlers so repeated calls don't pile up.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter: logging.Formatter
    if json:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(fmt=_CONSOLE_FORMAT, datefmt=_CONSOLE_DATEFMT)

    console_handler = logging.StreamHandler(stream=sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        # File logs are always JSON — easier to ingest downstream.
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    # Record app_name on the root logger for get_logger() lookups.
    setattr(root, _APP_NAME_ATTR, app_name)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger prefixed with the configured ``app_name``.

    The returned logger inherits from the root logger's handlers and level,
    so calling :func:`setup_logging` first is required for output to appear.
    """
    root = logging.getLogger()
    app_name = getattr(root, _APP_NAME_ATTR, None)
    if app_name and not name.startswith(f"{app_name}."):
        full_name = f"{app_name}.{name}" if name else app_name
    else:
        full_name = name or (app_name or "app")
    return logging.getLogger(full_name)


__all__ = [
    "JsonFormatter",
    "setup_logging",
    "get_logger",
]
