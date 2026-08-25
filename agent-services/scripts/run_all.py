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
import time
from collections.abc import Awaitable
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


async def _safe_run(label: str, coro: Awaitable[Any]) -> dict[str, Any]:
    """Run a single app cycle and capture per-app status.

    Returns a status dict with the shape::

        {
            "label": str,
            "ok": bool,         # False only if the coroutine raised
            "duration_s": float,
            "items": int,       # post-processed by callers (e.g. via value)
            "error": str | None,
            "value": Any,       # raw coroutine return on success, else None
        }

    ``ok`` defaults to ``True`` when the coroutine resolves without raising.
    Callers that care about a specific success signal (e.g. ``send_heartbeat``
    returning ``False``) post-process the ``value`` field and may override
    ``ok``. The dict shape makes per-app timing/items/visibility trivial to
    surface in the heartbeat's ``🩺 Status`` field and ``last_run.json``.
    """
    started = time.perf_counter()
    try:
        value = await coro
    except Exception as exc:  # noqa: BLE001
        duration = time.perf_counter() - started
        logger.exception("%s failed: %s", label, exc)
        return {
            "label": label,
            "ok": False,
            "duration_s": duration,
            "items": 0,
            "error": str(exc),
            "value": None,
        }
    duration = time.perf_counter() - started
    return {
        "label": label,
        "ok": True,
        "duration_s": duration,
        "items": 0,
        "error": None,
        "value": value,
    }


_LAST_RUN_PATH = _PROJECT_ROOT / "data" / "last_run.json"


def _read_previous_run() -> dict[str, Any] | None:
    """Load the previous run's summary from disk, or ``None`` if absent.

    Used to populate the heartbeat's ``Last successful run: …`` footer so
    operators see at a glance how stale a missed run is. A missing or
    malformed file is treated as "first run" rather than an error — we
    never want a status file to crash the cron.
    """
    if not _LAST_RUN_PATH.exists():
        return None
    try:
        import json
        with open(_LAST_RUN_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read last_run.json (%s) — treating as first run", exc)
        return None
    return data if isinstance(data, dict) else None


def _write_last_run(payload: dict[str, Any]) -> None:
    """Persist the current run's summary for the *next* heartbeat footer.

    Strips the ``value`` field from each app entry (raw coroutine returns
    are not JSON-serialisable and not useful to persist). Called at the end
    of :func:`run_all_once` after the heartbeat has been sent.
    """
    import json
    _LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {
        **payload,
        "apps": [
            {k: v for k, v in app.items() if k != "value"}
            for app in payload.get("apps", [])
        ],
    }
    with open(_LAST_RUN_PATH, "w", encoding="utf-8") as f:
        json.dump(serialisable, f, indent=2, sort_keys=False)
        f.write("\n")


async def run_all_once(
    news_cfg: NewsDigestConfig,
    etf_cfg: EtfSignalConfig,
    currency_cfg: CurrencyNotifierConfig,
) -> bool:
    """Run each app once and send a consolidated Heartbeat.

    Returns ``True`` if the heartbeat was delivered to Discord, ``False``
    otherwise. The boolean drives the process exit code so cron failures
    become visible in CI and in operator alerts. App-level failures are
    logged and reported in the ``🩺 Status`` field but do not by themselves
    cause a non-zero exit — only a failed heartbeat does, because a missing
    heartbeat is the user-visible failure mode.
    """
    from datetime import UTC, datetime

    from common.database import Database
    from common.heartbeat import send_heartbeat
    from news_digest.pipeline import _last_digest_text, parse_digest_sections

    logger.info("Running all apps once (--once mode)")
    previous_run = _read_previous_run()
    statuses: list[dict[str, Any]] = []

    # 1. News digest — run and capture output via the ContextVar side-channel.
    news_status = await _safe_run("news-digest", news_run(news_cfg))
    digest_text = _last_digest_text.get()
    if digest_text:
        _, sections = parse_digest_sections(digest_text)
        news_status["items"] = len(sections)
    statuses.append(news_status)

    # 2. ETF signal
    etf_db_path = _PROJECT_ROOT / "data" / "etf_signal.db"
    etf_status = await _safe_run(
        "etf-signal",
        etf_run(etf_cfg, db_path=etf_db_path, return_all_data=True),
    )
    if isinstance(etf_status["value"], list):
        etf_status["items"] = len(etf_status["value"])
    statuses.append(etf_status)

    # 3. Currency notifier
    currency_db_path = _PROJECT_ROOT / "data" / "currency_notifier.db"
    db = Database(currency_db_path)
    try:
        currency_status = await _safe_run(
            "currency-notifier",
            currency_run(currency_cfg, db, return_all_data=True),
        )
    finally:
        db.close()
    if isinstance(currency_status["value"], list):
        currency_status["items"] = len(currency_status["value"])
    statuses.append(currency_status)

    # 4. Daily Heartbeat — consolidate everything into one final report.
    # The send_heartbeat coroutine returns ``bool``; that bool is captured
    # in ``value`` and used to override ``ok`` (a False return without an
    # exception is still a failure for the operator).
    logger.info("Generating Daily Heartbeat report...")
    heartbeat_status = await _safe_run(
        "heartbeat",
        send_heartbeat(
            currency_data=currency_status["value"] or [],
            etf_data=etf_status["value"] or [],
            news_digest=digest_text or "",
            discord_config=news_cfg.discord,
            statuses=statuses,
            previous_run=previous_run,
        ),
    )
    heartbeat_status["items"] = 1 if heartbeat_status["value"] else 0
    heartbeat_status["ok"] = bool(heartbeat_status["value"])
    statuses.append(heartbeat_status)

    # 5. Persist this run's summary for the *next* run's footer.
    _write_last_run({
        "timestamp": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "duration_s": sum(s["duration_s"] for s in statuses),
        "apps": statuses,
        "heartbeat_sent": heartbeat_status["ok"],
    })

    return heartbeat_status["ok"]


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
        log_file=Path(os.environ["AGENT_SERVICES_LOG_FILE"])
        if os.environ.get("AGENT_SERVICES_LOG_FILE")
        else None,
    )
    log = get_logger("run_all")

    if args.once:
        heartbeat_ok = asyncio.run(run_all_once(news_cfg, etf_cfg, currency_cfg))
        return 0 if heartbeat_ok else 1

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
