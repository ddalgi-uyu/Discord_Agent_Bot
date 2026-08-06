"""Tests for :mod:`common.scheduler`."""
from __future__ import annotations

import asyncio
import time

import pytest

from common.scheduler import AppScheduler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scheduler() -> AppScheduler:
    """A fresh ``AppScheduler`` per test, with deterministic UTC timezone."""
    return AppScheduler(timezone="UTC")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_add_interval_job_registers_with_correct_interval(scheduler: AppScheduler) -> None:
    counter = {"value": 0}

    def tick() -> None:
        counter["value"] += 1

    job_id = scheduler.add_interval_job(tick, seconds=60, job_id="ticker")

    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["id"] == job_id
    assert job["id"] == "ticker"
    # APScheduler's IntervalTrigger stringifies as ``interval[H:MM:SS]``;
    # 60 seconds == 0:01:00.
    assert job["trigger"].startswith("interval[")
    assert "0:01:00" in job["trigger"]
    # No start yet, but a next_run_time should be present.
    assert job["next_run_time"] is not None


def test_add_interval_job_generates_unique_id_when_unspecified(
    scheduler: AppScheduler,
) -> None:
    scheduler.add_interval_job(lambda: None, seconds=30)
    scheduler.add_interval_job(lambda: None, seconds=30)

    ids = {job["id"] for job in scheduler.list_jobs()}
    assert len(ids) == 2
    assert all(len(i) > 0 for i in ids)


def test_add_cron_job_registers_expression(scheduler: AppScheduler) -> None:
    job_id = scheduler.add_cron_job(lambda: None, cron="0 8 * * *", job_id="morning")

    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    # CronTrigger string includes ``hour='8'`` and ``minute='0'``.
    assert "hour='8'" in jobs[0]["trigger"]
    assert "minute='0'" in jobs[0]["trigger"]


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


def test_list_jobs_is_initially_empty(scheduler: AppScheduler) -> None:
    assert scheduler.list_jobs() == []


def test_list_jobs_returns_all_registered(scheduler: AppScheduler) -> None:
    scheduler.add_interval_job(lambda: None, seconds=10, job_id="a")
    scheduler.add_interval_job(lambda: None, seconds=20, job_id="b")
    scheduler.add_cron_job(lambda: None, cron="*/5 * * * *", job_id="c")

    jobs = scheduler.list_jobs()
    ids = {job["id"] for job in jobs}
    assert ids == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Lifecycle (non-blocking)
# ---------------------------------------------------------------------------


async def test_start_background_then_shutdown_does_not_hang(
    scheduler: AppScheduler,
) -> None:
    scheduler.add_interval_job(lambda: None, seconds=1, job_id="noop")

    started = time.monotonic()
    scheduler.start_background()
    started_ok = time.monotonic() - started
    # start_background must return promptly (< 2s).
    assert started_ok < 2.0

    shutdown_started = time.monotonic()
    scheduler.shutdown(wait=False)
    shutdown_ok = time.monotonic() - shutdown_started
    # Shutdown must also return promptly.
    assert shutdown_ok < 2.0


async def test_shutdown_is_idempotent(scheduler: AppScheduler) -> None:
    scheduler.start_background()
    scheduler.shutdown(wait=False)
    # Calling shutdown again must not raise.
    scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def test_interval_job_actually_runs(scheduler: AppScheduler) -> None:
    """The registered callable must fire at least once within the test window."""
    counter = {"value": 0}

    def tick() -> None:
        counter["value"] += 1

    scheduler.add_interval_job(tick, seconds=1, job_id="counter")
    scheduler.start_background()
    try:
        # Wait long enough for at least one firing (interval=1s + small slack).
        # We use asyncio.sleep to yield to the scheduler — never time.sleep.
        for _ in range(40):
            if counter["value"] >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        scheduler.shutdown(wait=False)

    assert counter["value"] >= 1, "interval job did not fire within the test window"


async def test_async_callable_is_supported(scheduler: AppScheduler) -> None:
    """AsyncIOScheduler supports coroutine callables; ensure wrapper passes them through."""
    counter = {"value": 0}

    async def atick() -> None:
        counter["value"] += 1

    scheduler.add_interval_job(atick, seconds=1, job_id="acounter")
    scheduler.start_background()
    try:
        for _ in range(40):
            if counter["value"] >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        scheduler.shutdown(wait=False)

    assert counter["value"] >= 1
