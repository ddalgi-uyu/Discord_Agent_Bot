"""Shared async ``httpx.AsyncClient`` factory with retry support.

A single :func:`create_async_client` is the canonical entry point used by all
agent services. It builds an :class:`httpx.AsyncClient` whose transport is
wrapped by :class:`RetryTransport`, which retries idempotent failures
(5xx responses, connect/read timeouts) with exponential backoff before
giving up.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

import httpx

# Backoff schedule used when a request is retried (index == retry attempt).
# Attempt 0 waits 1s, attempt 1 waits 3s, attempt 2 waits 9s, etc.
DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 3.0, 9.0)


async def _async_sleep(seconds: float) -> None:
    """Default backoff primitive — wrapped here so tests can monkeypatch it."""
    await asyncio.sleep(seconds)


class RetryTransport(httpx.AsyncBaseTransport):
    """Async transport wrapper that retries on transient failures.

    Retries are triggered by:

    * 5xx HTTP responses
    * :class:`httpx.ConnectError`
    * :class:`httpx.ReadTimeout`

    Non-idempotent failures (4xx other than the connect/timeout cases) are
    surfaced immediately without retrying. The backoff between attempts is
    taken from ``backoff_schedule`` (defaults to ``DEFAULT_BACKOFF_SECONDS``);
    ``monkeypatch`` this tuple in tests for speed.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        max_retries: int = 3,
        backoff_schedule: Iterable[float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._transport = transport
        self._max_retries = max_retries
        self._backoff_schedule: tuple[float, ...] = tuple(
            backoff_schedule if backoff_schedule is not None else DEFAULT_BACKOFF_SECONDS
        )
        # ``sleep`` is parameterised so tests can replace it with an instant stub
        # without monkeypatching the global ``asyncio.sleep``.
        self._sleep = sleep or _async_sleep

    @property
    def max_retries(self) -> int:
        return self._max_retries

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        # ``max_retries`` retries means up to (max_retries + 1) total attempts.
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._transport.handle_async_request(request)
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_exc = exc
                response = None
            else:
                last_exc = None
                if response.status_code < 500:
                    return response
                # 5xx: fall through to retry logic below.

            # Do not sleep after the final attempt.
            if attempt >= self._max_retries:
                break

            backoff = self._backoff_schedule[min(attempt, len(self._backoff_schedule) - 1)]
            if backoff > 0:
                await self._sleep(backoff)

        if response is not None:
            return response
        assert last_exc is not None  # for type-checkers
        raise last_exc

    async def aclose(self) -> None:
        await self._transport.aclose()


def create_async_client(
    *,
    timeout: float = 10.0,
    max_retries: int = 3,
    user_agent: str = "agent-services/0.1",
) -> httpx.AsyncClient:
    """Create an :class:`httpx.AsyncClient` with retry, timeout and UA defaults.

    The returned client wraps :class:`httpx.AsyncHTTPTransport` in a
    :class:`RetryTransport` so transient failures (5xx, connect/read
    timeouts) are automatically retried with exponential backoff before
    being propagated to the caller.
    """
    headers = {"User-Agent": user_agent}
    retry_transport = RetryTransport(
        httpx.AsyncHTTPTransport(),
        max_retries=max_retries,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        transport=retry_transport,
    )
