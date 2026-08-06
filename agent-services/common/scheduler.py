"""Thin async-friendly wrapper around APScheduler's AsyncIOScheduler.

Provides interval and cron job registration with predictable identifiers and a
non-blocking lifecycle (``start_background``) that is suitable for tests.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


class AppScheduler:
    """Convenience wrapper over :class:`apscheduler.schedulers.asyncio.AsyncIOScheduler`.

    The wrapper keeps the underlying scheduler private and exposes a small,
    opinionated surface area (interval/cron registration, lifecycle, listing).
    Both synchronous and coroutine callables are accepted by APScheduler.
    """

    def __init__(self, timezone: str = "UTC") -> None:
        self._scheduler = AsyncIOScheduler(timezone=timezone)

    # ------------------------------------------------------------------ jobs

    def add_interval_job(
        self,
        func: Callable[..., Any],
        *,
        seconds: int = 3600,
        job_id: str = "",
        **kwargs: Any,
    ) -> str:
        """Add a job that runs every ``seconds`` seconds.

        Additional ``kwargs`` are forwarded to :class:`~apscheduler.triggers.interval.IntervalTrigger`
        (e.g. ``minutes=``, ``hours=``). When ``job_id`` is empty, a UUID4 is
        generated so each registration is uniquely addressable.
        """
        resolved_id = job_id or str(uuid.uuid4())
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=seconds, **kwargs),
            id=resolved_id,
        )
        return resolved_id

    def add_cron_job(
        self,
        func: Callable[..., Any],
        *,
        cron: str,
        job_id: str = "",
        **kwargs: Any,
    ) -> str:
        """Add a cron-triggered job.

        ``cron`` is a standard cron expression (e.g. ``'0 8 * * *'``). Extra
        ``kwargs`` are forwarded to :class:`~apscheduler.triggers.cron.CronTrigger.from_crontab`.
        """
        resolved_id = job_id or str(uuid.uuid4())
        self._scheduler.add_job(
            func,
            trigger=CronTrigger.from_crontab(cron, **kwargs),
            id=resolved_id,
        )
        return resolved_id

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start the scheduler (blocking entry-point for long-running apps).

        With ``AsyncIOScheduler`` the call returns immediately, but the
        scheduler continues running on the active asyncio event loop until
        :meth:`shutdown` is invoked. Use this from a long-lived ``main``
        coroutine that keeps the loop alive.
        """
        if not self._scheduler.running:
            self._scheduler.start()

    def start_background(self) -> None:
        """Start the scheduler in non-blocking mode (preferred in tests).

        Identical to :meth:`start` for ``AsyncIOScheduler`` because the
        underlying scheduler is already non-blocking — the separation exists
        purely to give call sites an explicit, intent-revealing name.
        """
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler, optionally waiting for in-flight jobs."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    # ------------------------------------------------------------------ query

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return a snapshot of registered jobs.

        Each entry contains ``id``, ``name``, ``func`` (the callable's
        qualified name), ``next_run_time`` (ISO-8601 string or ``None``),
        and ``trigger`` (its ``str()`` representation).

        When the underlying scheduler is stopped, jobs live in the pending
        queue and have no ``next_run_time`` yet; in that case we derive one
        from the trigger so callers get a useful value regardless of state.
        """
        snapshot: list[dict[str, Any]] = []
        for job in self._scheduler.get_jobs():
            next_run: datetime | None = getattr(job, "next_run_time", None)
            if next_run is None:
                next_run = job.trigger.get_next_fire_time(
                    None, datetime.now(UTC)
                )
            snapshot.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "func": (
                        f"{job.func.__module__}.{job.func.__qualname__}"
                        if getattr(job, "func", None) is not None
                        else None
                    ),
                    "next_run_time": next_run.isoformat() if next_run is not None else None,
                    "trigger": str(job.trigger),
                }
            )
        return snapshot
