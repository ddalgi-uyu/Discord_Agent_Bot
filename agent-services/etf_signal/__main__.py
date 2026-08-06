"""CLI entry point for the ``etf-signal`` application.

Mirrors the ``news-digest`` style: ``argparse``-driven flags, ``asyncio``
for the pipeline coroutine, and :func:`common.config_loader.load_config`
for configuration. A market-hours gate (US equity session, Mon–Fri
09:30–16:00 ET) skips the run when the market is closed; pass ``--once``
to bypass the gate.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from common.config_loader import load_config
from common.logging_setup import setup_logging

from etf_signal.config import EtfSignalConfig
from etf_signal.pipeline import DEFAULT_DB_PATH, run

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# US equity market hours
# ---------------------------------------------------------------------------

_MARKET_TZ: ZoneInfo = ZoneInfo("US/Eastern")
_MARKET_OPEN_MINUTES: int = 9 * 60 + 30  # 09:30 ET
_MARKET_CLOSE_MINUTES: int = 16 * 60      # 16:00 ET
_MARKET_WEEKDAYS: frozenset[int] = frozenset({0, 1, 2, 3, 4})  # Mon..Fri

# Shared Discord webhook env-var name. Mirrors ``.env.example`` so callers
# can set ``DISCORD_WEBHOOK_URL`` once for every agent-service in the
# monorepo.
_DISCORD_WEBHOOK_ENV: str = "DISCORD_WEBHOOK_URL"


# ---------------------------------------------------------------------------
# Market-hours gate
# ---------------------------------------------------------------------------


def is_market_open(now: datetime | None = None) -> bool:
    """Return ``True`` when the US equity market is currently in session.

    ``now`` defaults to the current local time in US/Eastern. Weekends and
    weekdays outside 09:30–16:00 ET both return ``False``. The closing
    minute is exclusive — the market is considered closed at 16:00:00.
    """
    current = now if now is not None else datetime.now(tz=_MARKET_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_MARKET_TZ)
    else:
        current = current.astimezone(_MARKET_TZ)
    if current.weekday() not in _MARKET_WEEKDAYS:
        return False
    minutes = current.hour * 60 + current.minute
    return _MARKET_OPEN_MINUTES <= minutes < _MARKET_CLOSE_MINUTES


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="etf-signal",
        description="ETF buy-signal detector and Discord notifier.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run a single pipeline iteration and exit, bypassing the "
            "market-hours gate."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/etf_signal.yaml"),
        help="Path to the YAML config file (default: config/etf_signal.yaml).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=(
            "Override the SQLite database path. Defaults to "
            "data/etf_signal.db (relative to CWD)."
        ),
    )
    return parser.parse_args(argv)


def _resolve_webhook_url(config: EtfSignalConfig) -> None:
    """Populate ``config.discord.webhook_url`` from the shared env var.

    The default ``etf_signal.yaml`` intentionally omits ``webhook_url`` so
    the same file can be committed to source control. We fall back to the
    monorepo-wide ``DISCORD_WEBHOOK_URL`` env var (matching
    ``.env.example``) when the field is empty.
    """
    if config.discord.webhook_url:
        return
    fallback = os.environ.get(_DISCORD_WEBHOOK_ENV)
    if fallback:
        config.discord.webhook_url = fallback


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------


async def _async_main(args: argparse.Namespace) -> int:
    config = load_config(
        app_name="etf-signal",
        yaml_path=args.config,
        config_class=EtfSignalConfig,
    )

    setup_logging(
        level=config.logging.level,
        json=config.logging.json,
        log_file=config.logging.file,
        app_name=config.app_name,
    )
    _resolve_webhook_url(config)

    if not args.once and not is_market_open():
        logger.info(
            "Outside US market hours (Mon-Fri 09:30-16:00 ET); skipping run. "
            "Re-run with --once to override."
        )
        return 0

    db_path = args.db if args.db is not None else DEFAULT_DB_PATH
    signals = await run(config, db_path=db_path)
    logger.info("Pipeline finished; %d signal(s) dispatched", signals)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Synchronous entry point used by ``python -m etf_signal`` and the
    console-script declared in ``pyproject.toml``.
    """
    args = _parse_args(argv)
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
