"""Command-line entry point for the ``currency-notifier`` service.

Usage examples::

    # One-shot poll (cron / systemd timer / manual run)
    python -m currency_notifier --once --config config/currency_notifier.yaml

    # Long-running daemon driven by the cron schedule in the config
    python -m currency_notifier --config config/currency_notifier.yaml

``--once`` runs a single polling cycle and exits, which is what most
production deployment patterns want (a daily cron entry invoking the
``--once`` mode is simpler than running a daemon). Without ``--once`` the
process stays alive and uses :class:`common.scheduler.AppScheduler` to
trigger the cycle.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from common.config_loader import load_config
from common.database import Database
from common.logging_setup import get_logger, setup_logging
from common.scheduler import AppScheduler

from currency_notifier.config import CurrencyNotifierConfig
from currency_notifier.pipeline import run as run_pipeline


APP_NAME = "currency-notifier"

#: Default cron schedule used when the YAML config does not specify one.
#: Frankfurter publishes rates once per business day, so a single 17:00 CET
#: poll is enough — anything more frequent just yields the same numbers.
DEFAULT_CRON = "0 17 * * *"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="currency-notifier",
        description="Daily Discord currency rate notifier (Frankfurter).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/currency_notifier.yaml"),
        help="Path to the YAML configuration file (default: %(default)s).",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="Optional .env file path; missing files are silently ignored.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory for the SQLite rate history database.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single polling cycle and exit (default: daemon mode).",
    )
    return parser


async def _run_once(config: CurrencyNotifierConfig, db_path: Path) -> int:
    """Run one pipeline cycle and return the number of alerts delivered."""
    with Database(db_path) as db:
        return await run_pipeline(config, db)


async def _daemon(config: CurrencyNotifierConfig, db_path: Path) -> None:
    """Long-running mode: schedule the pipeline per the YAML config."""
    logger = get_logger("daemon")

    cron_expr = config.schedule.cron or DEFAULT_CRON
    timezone = config.schedule.timezone or "UTC"

    scheduler = AppScheduler(timezone=timezone)
    logger.info(
        "Scheduling %s with cron=%r in tz=%s (DB=%s)",
        APP_NAME,
        cron_expr,
        timezone,
        db_path,
    )

    job_id = f"{APP_NAME}-poll"

    async def _job() -> None:
        try:
            with Database(db_path) as db:
                delivered = await run_pipeline(config, db)
            logger.info("Pipeline cycle complete: %d alert(s) delivered", delivered)
        except Exception as exc:  # noqa: BLE001 — never kill the daemon
            logger.exception("Pipeline cycle crashed: %s", exc)

    scheduler.add_cron_job(_job, cron=cron_expr, job_id=job_id)

    scheduler.start_background()

    # Block until SIGINT / SIGTERM, then shut the scheduler down cleanly.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        logger.info("Stop signal received; shutting down")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            # Signal handlers are unavailable on Windows for proactor loops
            # in some Python versions — fall back to keyboard polling.
            pass

    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        config = load_config(
            app_name=APP_NAME,
            yaml_path=args.config,
            env_path=args.env if args.env.exists() else None,
            config_class=CurrencyNotifierConfig,
        )
    except Exception as exc:
        # ``load_config`` already wraps everything in ConfigError; re-raise
        # so the traceback is visible but log a single human-readable line
        # for the operator first.
        sys.stderr.write(f"Failed to load config: {exc}\n")
        return 2

    setup_logging(
        level=config.logging.level,
        json=config.logging.json,
        log_file=config.logging.file,
        app_name=config.app_name,
    )
    logger = get_logger("main")
    logger.info(
        "Starting %s (pairs=%d, once=%s)", config.app_name, len(config.pairs), args.once
    )

    args.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.data_dir / f"{APP_NAME}.db"

    if args.once:
        delivered = asyncio.run(_run_once(config, db_path))
        logger.info("Single cycle complete: %d alert(s) delivered", delivered)
        return 0

    try:
        asyncio.run(_daemon(config, db_path))
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
