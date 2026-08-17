"""Unified scheduler that runs all 3 agent-service apps in a single process.

Usage::

    python scripts/run_all.py           # start all 3 schedulers
    python scripts/run_all.py --once    # run each app once, then exit

Each app runs on its own cron/interval schedule as defined in its YAML config.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from common.config_loader import load_config
from common.discord_notifier import create_notifier
from common.exceptions import NotifyError
from common.logging_setup import get_logger, setup_logging
from common.scheduler import AppScheduler

from currency_notifier.config import CurrencyNotifierConfig
from currency_notifier.pipeline import run as currency_run
from etf_signal.config import EtfSignalConfig
from etf_signal.pipeline import run as etf_run
from news_digest.config import NewsDigestConfig
from news_digest.pipeline import run as news_run

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_CONFIG_DIR = _PROJECT_ROOT / "config"
_ENV_PATH = _PROJECT_ROOT / ".env"


def _load_configs() -> tuple[NewsDigestConfig, EtfSignalConfig, CurrencyNotifierConfig]:
    """Load all three app configs from their YAML files."""
    env_path = _ENV_PATH if _ENV_PATH.exists() else None

    news_cfg = load_config(
        "news-digest",
        _CONFIG_DIR / "news_digest.yaml",
        env_path=env_path,
        config_class=NewsDigestConfig,
    )
    etf_cfg = load_config(
        "etf-signal",
        _CONFIG_DIR / "etf_signal.yaml",
        env_path=env_path,
        config_class=EtfSignalConfig,
    )
    currency_cfg = load_config(
        "currency-notifier",
        _CONFIG_DIR / "currency_notifier.yaml",
        env_path=env_path,
        config_class=CurrencyNotifierConfig,
    )
    return news_cfg, etf_cfg, currency_cfg


def _resolve_webhook_urls(
    news_cfg: NewsDigestConfig,
    etf_cfg: EtfSignalConfig,
    currency_cfg: CurrencyNotifierConfig,
) -> None:
    """Populate missing webhook URLs from the shared DISCORD_WEBHOOK_URL env var."""
    shared_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not shared_url:
        return
    for cfg in (news_cfg, etf_cfg, currency_cfg):
        if not cfg.discord.webhook_url:
            cfg.discord.webhook_url = shared_url


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


async def _safe_run(label: str, coro) -> Any:
    """Run a single app cycle, catching and logging any exception.

    Returns the inner coroutine's result on success, or ``None`` on failure
    so that callers can keep going without aborting the consolidated
    Heartbeat. The original implementation returned ``None`` unconditionally,
    which caused ``run_all_once`` to feed empty data into ``send_heartbeat``.
    """
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s failed: %s", label, exc)
        return None


async def run_all_once(
    news_cfg: NewsDigestConfig,
    etf_cfg: EtfSignalConfig,
    currency_cfg: CurrencyNotifierConfig,
) -> None:
    """Run each app once (sequentially for safety) and send a consolidated Heartbeat.
    """
    from common.database import Database
    from common.heartbeat import send_heartbeat

    logger.info("Running all apps once (--once mode)")

    # 1. News digest — run and capture output
    # Note: news_run returns the digest string (markdown)
    news_digest = await _safe_run("news-digest", news_run(news_cfg))

    # 2. ETF signal — run and capture data
    etf_db_path = _PROJECT_ROOT / "data" / "etf_signal.db"
    etf_data = await _safe_run("etf-signal", etf_run(etf_cfg, db_path=etf_db_path, return_all_data=True))

    # 3. Currency notifier — run and capture data
    currency_db_path = _PROJECT_ROOT / "data" / "currency_notifier.db"
    db = Database(currency_db_path)
    try:
        currency_data = await _safe_run("currency-notifier", currency_run(currency_cfg, db, return_all_data=True))
    finally:
        db.close()

    # 4. Daily Heartbeat — consolidate everything into one final report
    logger.info("Generating Daily Heartbeat report...")
    await _safe_run(
        "heartbeat", 
        send_heartbeat(
            currency_data=currency_data or [],
            etf_data=etf_data or [],
            news_digest=news_digest or "",
            discord_config=news_cfg.discord
        )
    )


def schedule_all(
    news_cfg: NewsDigestConfig,
    etf_cfg: EtfSignalConfig,
    currency_cfg: CurrencyNotifierConfig,
) -> AppScheduler:
    """Create an AppScheduler with all three apps registered."""
    from common.database import Database

    # Use the most permissive timezone (UTC) as the scheduler base.
    scheduler = AppScheduler(timezone="UTC")

    # --- news-digest (daily cron) ---
    news_cron = news_cfg.schedule.cron or "0 8 * * *"
    scheduler.add_cron_job(
        lambda: asyncio.run(_safe_run("news-digest", news_run(news_cfg))),
        cron=news_cron,
        job_id="news-digest",
    )
    logger.info("Registered news-digest  cron=%s", news_cron)

    # --- etf-signal (market-hours cron) ---
    etf_cron = etf_cfg.schedule.cron or "0 9-16 * * mon-fri"
    scheduler.add_cron_job(
        lambda: asyncio.run(
            _safe_run(
                "etf-signal",
                etf_run(etf_cfg, db_path=_PROJECT_ROOT / "data" / "etf_signal.db"),
            )
        ),
        cron=etf_cron,
        job_id="etf-signal",
    )
    logger.info("Registered etf-signal   cron=%s", etf_cron)

    # --- currency-notifier (daily cron) ---
    currency_cron = currency_cfg.schedule.cron or "0 17 * * mon-fri"
    currency_db_path = _PROJECT_ROOT / "data" / "currency_notifier.db"

    async def _currency_job() -> None:
        db = Database(currency_db_path)
        try:
            await _safe_run("currency-notifier", currency_run(currency_cfg, db))
        finally:
            db.close()

    scheduler.add_cron_job(
        lambda: asyncio.run(_currency_job()),
        cron=currency_cron,
        job_id="currency-notifier",
    )
    logger.info("Registered currency-notifier cron=%s", currency_cron)

    return scheduler


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run_all",
        description="Unified scheduler for all agent-service apps",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run each app once and exit.",
    )
    args = parser.parse_args()

    news_cfg, etf_cfg, currency_cfg = _load_configs()
    _resolve_webhook_urls(news_cfg, etf_cfg, currency_cfg)

    setup_logging(
        level=news_cfg.logging.level,
        json=news_cfg.logging.json,
        app_name="unified",
    )
    log = get_logger("run_all")

    if args.once:
        asyncio.run(run_all_once(news_cfg, etf_cfg, currency_cfg))
        return 0

    scheduler = schedule_all(news_cfg, etf_cfg, currency_cfg)

    log.info("Starting unified scheduler (3 apps registered)")

    async def _serve() -> None:
        scheduler.start()
        stop = asyncio.Event()
        try:
            await stop.wait()
        finally:
            scheduler.shutdown(wait=False)

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
