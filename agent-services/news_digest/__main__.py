"""News digest service entry point.

Two operating modes:

* ``python -m news_digest --once`` — run a single digest cycle and exit.
* ``python -m news_digest`` (default) — run on the schedule defined in
  ``config.news_digest.yaml`` until interrupted.

The CLI accepts two optional flags:

* ``--once``             run a single cycle without starting the scheduler.
* ``--config <path>``    override the default config YAML location.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from common.config_loader import load_config
from common.exceptions import ConfigError
from common.logging_setup import get_logger, setup_logging
from common.scheduler import AppScheduler
from news_digest.config import NewsDigestConfig
from news_digest.pipeline import run

__all__ = ["main", "_scheduled_run"]

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "news_digest.yaml"
DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------


async def _scheduled_run(config: NewsDigestConfig) -> None:
    """Wrap :func:`news_digest.pipeline.run` so APScheduler sees an awaitable.

    Any exception inside ``run`` is already swallowed by the pipeline itself;
    we add an extra safety net here so a crash in one job does not stop the
    scheduler.
    """
    job_logger = get_logger("scheduled")
    try:
        await run(config)
    except Exception as exc:  # noqa: BLE001 — scheduler should keep ticking
        job_logger.error(
            "scheduled cycle raised unexpectedly: %s",
            exc,
            exc_info=True,
        )


async def _serve(scheduler: AppScheduler) -> None:
    """Start ``scheduler`` and block until cancelled.

    ``AsyncIOScheduler`` is non-blocking — we just need to keep the asyncio
    event loop alive until the user interrupts.
    """
    scheduler.start()
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="news-digest",
        description="Fetch RSS feeds, summarise them via an LLM, and post to Discord.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single digest cycle and exit (do not start the scheduler).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to the YAML config file. "
            f"Defaults to {DEFAULT_CONFIG_PATH}."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point declared in ``pyproject.toml`` (``news-digest = …``)."""
    args = _parse_args(argv)

    config_path: Path = args.config or DEFAULT_CONFIG_PATH
    env_path = DEFAULT_ENV_PATH if DEFAULT_ENV_PATH.exists() else None

    try:
        config = load_config(
            "news-digest",
            config_path,
            env_path=env_path,
            config_class=NewsDigestConfig,
        )
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        level=config.logging.level,
        json=config.logging.json,
        log_file=config.logging.file,
        app_name=config.app_name,
    )

    main_logger = get_logger("main")

    # ---- single-shot mode --------------------------------------------------
    if args.once:
        main_logger.info("running a single digest cycle (--once)")
        try:
            asyncio.run(run(config))
        except KeyboardInterrupt:
            main_logger.info("interrupted by user")
            return 130
        return 0

    # ---- scheduler mode ----------------------------------------------------
    if not config.schedule.cron:
        main_logger.error(
            "schedule.cron is not set in %s; cannot start scheduler",
            config_path,
        )
        return 2

    main_logger.info(
        "starting scheduler (cron=%r timezone=%s)",
        config.schedule.cron,
        config.schedule.timezone,
    )

    scheduler = AppScheduler(timezone=config.schedule.timezone)
    scheduler.add_cron_job(
        _scheduled_run,
        cron=config.schedule.cron,
        job_id="news-digest-cycle",
    )

    try:
        asyncio.run(_serve(scheduler))
    except KeyboardInterrupt:
        main_logger.info("interrupted — shutting down")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
