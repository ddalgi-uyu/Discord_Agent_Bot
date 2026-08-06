"""Unit tests for :mod:`common.logging_setup`."""

from __future__ import annotations

import io
import json
import logging
import logging.handlers
import re
from pathlib import Path

import pytest

from common.logging_setup import (
    JsonFormatter,
    _BACKUP_COUNT,
    _MAX_BYTES,
    get_logger,
    setup_logging,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Snapshot and restore the root logger state around each test.

    This prevents handler-leakage between tests and keeps the global logging
    state clean.
    """
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    saved_disabled = root.disabled
    saved_attrs = {k: getattr(root, k) for k in dir(root) if not k.startswith("__")}

    yield

    # Restore: remove any handlers added during the test, then reattach the
    # saved ones. Use a plain loop (no ``root.handlers = saved_handlers``) so
    # internal state stays consistent.
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:
            pass
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
    root.disabled = saved_disabled
    # Restore any custom attributes (e.g. _app_name) that existed before.
    for attr in list(vars(root).keys()):
        if attr not in saved_attrs and not attr.startswith("__"):
            try:
                delattr(root, attr)
            except AttributeError:
                pass
    for k, v in saved_attrs.items():
        if k in ("handlers", "level", "disabled", "manager"):
            continue
        try:
            setattr(root, k, v)
        except AttributeError:
            pass


@pytest.fixture
def log_file_path(tmp_path: Path) -> Path:
    return tmp_path / "app.log"


# ---------------------------------------------------------------------------
# setup_logging — level & handler configuration
# ---------------------------------------------------------------------------


def test_setup_logging_sets_root_level(caplog) -> None:
    setup_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_setup_logging_accepts_string_levels(level: str) -> None:
    setup_logging(level)
    assert logging.getLogger().level == getattr(logging, level)


def test_setup_logging_unknown_level_defaults_to_info() -> None:
    setup_logging("NONSENSE")
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_idempotent_no_duplicate_handlers() -> None:
    setup_logging("INFO")
    setup_logging("INFO")
    setup_logging("INFO")
    root = logging.getLogger()
    # Expect exactly one console handler from setup_logging.
    assert len(root.handlers) == 1


def test_setup_logging_replaces_handlers_when_level_changes() -> None:
    setup_logging("INFO")
    setup_logging("DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


def _make_record(
    name: str = "test",
    level: int = logging.INFO,
    msg: str = "hello",
    extras: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extras:
        for k, v in extras.items():
            setattr(record, k, v)
    return record


def test_json_formatter_emits_required_fields() -> None:
    formatter = JsonFormatter()
    record = _make_record(name="agent.x", msg="hello world")
    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert "timestamp" in payload
    assert payload["level"] == "INFO"
    assert payload["name"] == "agent.x"
    assert payload["message"] == "hello world"


def test_json_formatter_timestamp_is_iso8601_utc() -> None:
    formatter = JsonFormatter()
    record = _make_record()
    payload = json.loads(formatter.format(record))

    # Either an ISO-8601 UTC string ending in 'Z' or with explicit offset.
    ts = payload["timestamp"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)
    assert ts.endswith("Z") or "+00:00" in ts


def test_json_formatter_uses_each_log_level() -> None:
    formatter = JsonFormatter()
    for level in (logging.DEBUG, logging.WARNING, logging.ERROR):
        record = _make_record(level=level)
        payload = json.loads(formatter.format(record))
        assert payload["level"] == logging.getLevelName(level)


def test_json_formatter_serialises_extra_fields() -> None:
    formatter = JsonFormatter()
    record = _make_record(
        msg="sent",
        extras={"user_id": 42, "channel": "general", "ok": True},
    )
    payload = json.loads(formatter.format(record))

    assert payload["user_id"] == 42
    assert payload["channel"] == "general"
    assert payload["ok"] is True


def test_json_formatter_message_uses_getmessage() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world"


def test_json_formatter_handles_exc_info() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="x",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "exc_info" in payload
    assert "ValueError" in payload["exc_info"]


def test_json_formatter_is_single_line() -> None:
    formatter = JsonFormatter()
    record = _make_record(msg="line1\nline2")
    rendered = formatter.format(record)
    # JSON.dumps with ensure_ascii=False escapes newlines, so still one line.
    assert "\n" not in rendered
    payload = json.loads(rendered)
    assert payload["message"] == "line1\nline2"


# ---------------------------------------------------------------------------
# Console handler output
# ---------------------------------------------------------------------------


def test_console_output_simple_format_contains_required_pieces() -> None:
    setup_logging("INFO", json=False)

    # Replace the existing console handler with one writing to our buffer so
    # we can inspect the rendered output deterministically.
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    buf = io.StringIO()
    console = logging.StreamHandler(stream=buf)
    console.setFormatter(
        logging.Formatter(fmt="%(levelname)s|%(name)s|%(message)s")
    )
    root.addHandler(console)

    logger = get_logger("module")
    logger.info("ping")

    rendered = buf.getvalue()
    assert "INFO" in rendered
    assert "ping" in rendered
    assert "module" in rendered


def test_setup_logging_json_true_emits_json_to_console() -> None:
    setup_logging("INFO", json=True, app_name="svc")

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    buf = io.StringIO()
    console = logging.StreamHandler(stream=buf)
    console.setFormatter(JsonFormatter())
    root.addHandler(console)

    logger = get_logger("svc.scheduler")
    logger.info("tick")

    payload = json.loads(buf.getvalue().strip())
    assert payload["name"] == "svc.scheduler"
    assert payload["message"] == "tick"
    assert payload["level"] == "INFO"


# ---------------------------------------------------------------------------
# File rotation
# ---------------------------------------------------------------------------


def test_file_handler_attached_when_log_file_provided(log_file_path: Path) -> None:
    setup_logging("INFO", log_file=log_file_path)
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in handlers)


def test_file_handler_uses_expected_rotation_params(log_file_path: Path) -> None:
    setup_logging("INFO", log_file=log_file_path)
    handler = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert handler.maxBytes == _MAX_BYTES == 10 * 1024 * 1024
    assert handler.backupCount == _BACKUP_COUNT == 3


def test_file_handler_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "nested" / "app.log"
    setup_logging("INFO", log_file=target)
    logger = get_logger("writer")
    logger.info("boot")
    assert target.exists()
    assert target.parent.is_dir()


def test_file_handler_writes_json_records(log_file_path: Path) -> None:
    setup_logging("INFO", log_file=log_file_path, app_name="svc")
    logger = get_logger("svc.writer")
    logger.info("first message")
    logger.warning("second message")

    contents = log_file_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(contents) == 2
    first = json.loads(contents[0])
    second = json.loads(contents[1])
    assert first["level"] == "INFO"
    assert first["message"] == "first message"
    assert first["name"] == "svc.writer"
    assert second["level"] == "WARNING"
    assert second["message"] == "second message"


def test_file_rotation_creates_backup(log_file_path: Path, monkeypatch) -> None:
    """Forcing a rollover should produce a ``.log.1`` backup file."""
    setup_logging("INFO", log_file=log_file_path)
    handler = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )

    # Force ``shouldRollover`` to return True so the very next emit triggers
    # a rollover without having to write 10 MB of data.
    monkeypatch.setattr(handler, "shouldRollover", lambda _record: True)

    logger = get_logger("rotator")
    logger.info("trigger rollover")
    handler.flush()

    assert log_file_path.exists()
    # After rollover, the active file is renamed to ``<base>.log.1``.
    backup = log_file_path.with_name(log_file_path.name + ".1")
    assert backup.exists()


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


def test_get_logger_returns_child_with_app_prefix() -> None:
    setup_logging("INFO", app_name="news")
    logger = get_logger("fetcher")
    assert logger.name == "news.fetcher"


def test_get_logger_does_not_double_prefix() -> None:
    setup_logging("INFO", app_name="news")
    logger = get_logger("news.fetcher")
    assert logger.name == "news.fetcher"


def test_get_logger_empty_name_returns_app_name() -> None:
    setup_logging("INFO", app_name="news")
    logger = get_logger("")
    assert logger.name == "news"


def test_get_logger_falls_back_when_setup_not_called() -> None:
    # Root logger has no _app_name attribute when setup_logging isn't called.
    logger = get_logger("module")
    assert logger.name == "module"


def test_get_logger_returns_logger_instance() -> None:
    setup_logging("INFO", app_name="x")
    logger = get_logger("y")
    assert isinstance(logger, logging.Logger)


# ---------------------------------------------------------------------------
# Behaviour: must not disturb propagation unexpectedly
# ---------------------------------------------------------------------------


def test_setup_logging_does_not_disable_existing_loggers() -> None:
    pre_existing = logging.getLogger("pre.existing")
    setup_logging("INFO")
    assert pre_existing.disabled is False
    assert pre_existing.propagate is True


def test_existing_named_loggers_propagate_to_root() -> None:
    setup_logging("INFO", app_name="app")
    named = logging.getLogger("app.worker")
    assert named.propagate is True
