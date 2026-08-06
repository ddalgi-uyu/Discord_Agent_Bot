"""Tests for :mod:`common.http_client`."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx

from common.http_client import RetryTransport, create_async_client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def instant_sleep() -> Callable[[float], Awaitable[None]]:
    """An async no-op that pretends to sleep, so retry tests are instant."""

    async def _noop(_seconds: float) -> None:
        return None

    return _noop


def _router_mock_transport(router: respx.Router) -> httpx.MockTransport:
    """Wrap a :class:`respx.Router` in an :class:`httpx.MockTransport`.

    Placing this *inside* a :class:`RetryTransport` is what lets the retry
    wrapper observe 5xx responses from respx and exercise the retry path.
    """
    return httpx.MockTransport(router.async_handler)


# ---------------------------------------------------------------------------
# Success path (create_async_client + respx.mock)
# ---------------------------------------------------------------------------


async def test_200_response_returns_immediately() -> None:
    """A 200 response must be returned without retries."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://example.com/api").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        client = create_async_client(max_retries=3)
        try:
            response = await client.get("https://example.com/api")
        finally:
            await client.aclose()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Retry on 5xx
# ---------------------------------------------------------------------------


async def test_503_triggers_retries(instant_sleep: Callable[[float], Awaitable[None]]) -> None:
    """A 503 response must be retried ``max_retries`` extra times."""
    router = respx.Router(assert_all_called=False)
    route = router.get("https://example.com/api").mock(return_value=httpx.Response(503))

    retry = RetryTransport(
        _router_mock_transport(router),
        max_retries=3,
        sleep=instant_sleep,
    )
    async with httpx.AsyncClient(transport=retry) as client:
        response = await client.get("https://example.com/api")

    assert response.status_code == 503
    # 1 initial attempt + 3 retries = 4 calls.
    assert route.call_count == 4


async def test_500_triggers_retries(instant_sleep: Callable[[float], Awaitable[None]]) -> None:
    """Any 5xx status code must trigger a retry, not just 503."""
    router = respx.Router(assert_all_called=False)
    route = router.get("https://example.com/api").mock(return_value=httpx.Response(500))

    retry = RetryTransport(
        _router_mock_transport(router),
        max_retries=2,
        sleep=instant_sleep,
    )
    async with httpx.AsyncClient(transport=retry) as client:
        response = await client.get("https://example.com/api")

    assert response.status_code == 500
    assert route.call_count == 3  # 1 + 2 retries


async def test_4xx_is_not_retried(instant_sleep: Callable[[float], Awaitable[None]]) -> None:
    """4xx responses are client errors and must not trigger retries."""
    router = respx.Router(assert_all_called=False)
    route = router.get("https://example.com/api").mock(return_value=httpx.Response(404))

    retry = RetryTransport(
        _router_mock_transport(router),
        max_retries=3,
        sleep=instant_sleep,
    )
    async with httpx.AsyncClient(transport=retry) as client:
        response = await client.get("https://example.com/api")

    assert response.status_code == 404
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Retry on transport errors
# ---------------------------------------------------------------------------


async def test_connect_error_triggers_retries(
    instant_sleep: Callable[[float], Awaitable[None]],
) -> None:
    """``ConnectError`` must be caught and retried."""
    router = respx.Router(assert_all_called=False)
    route = router.get("https://example.com/api").mock(
        side_effect=httpx.ConnectError("boom")
    )

    retry = RetryTransport(
        _router_mock_transport(router),
        max_retries=3,
        sleep=instant_sleep,
    )
    async with httpx.AsyncClient(transport=retry) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://example.com/api")

    assert route.call_count == 4  # 1 + 3 retries


async def test_read_timeout_triggers_retries(
    instant_sleep: Callable[[float], Awaitable[None]],
) -> None:
    """``ReadTimeout`` must be caught and retried."""
    router = respx.Router(assert_all_called=False)
    route = router.get("https://example.com/api").mock(
        side_effect=httpx.ReadTimeout("slow")
    )

    retry = RetryTransport(
        _router_mock_transport(router),
        max_retries=2,
        sleep=instant_sleep,
    )
    async with httpx.AsyncClient(transport=retry) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.get("https://example.com/api")

    assert route.call_count == 3  # 1 + 2 retries


async def test_recoverable_503_then_success(
    instant_sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Once a retry produces a 2xx, the response is returned and we stop retrying."""
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(502),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    router = respx.Router(assert_all_called=False)
    route = router.get("https://example.com/api").mock(side_effect=handler)

    retry = RetryTransport(
        _router_mock_transport(router),
        max_retries=5,
        sleep=instant_sleep,
    )
    async with httpx.AsyncClient(transport=retry) as client:
        response = await client.get("https://example.com/api")

    assert response.status_code == 200
    assert route.call_count == 3  # 503, 502, then 200


# ---------------------------------------------------------------------------
# Timeout / User-Agent
# ---------------------------------------------------------------------------


async def test_create_async_client_sets_timeout() -> None:
    """``create_async_client(timeout=...)`` must propagate to ``AsyncClient``."""
    client = create_async_client(timeout=12.5, max_retries=0)
    try:
        # httpx stores timeout as a Timeout instance; verify the read budget
        # matches what we passed in (in seconds).
        assert client.timeout.connect == 12.5
        assert client.timeout.read == 12.5
        assert client.timeout.write == 12.5
        assert client.timeout.pool == 12.5
    finally:
        await client.aclose()


async def test_create_async_client_sets_user_agent() -> None:
    """The default ``User-Agent`` header must be applied to outgoing requests."""
    with respx.mock(assert_all_called=True) as router:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent")
            return httpx.Response(200)

        router.get("https://example.com/api").mock(side_effect=handler)

        client = create_async_client()
        try:
            response = await client.get("https://example.com/api")
        finally:
            await client.aclose()

    assert response.status_code == 200
    assert captured["ua"] == "agent-services/0.1"


async def test_create_async_client_uses_custom_user_agent() -> None:
    """A caller-supplied ``user_agent`` must override the default."""
    with respx.mock(assert_all_called=True) as router:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent")
            return httpx.Response(200)

        router.get("https://example.com/api").mock(side_effect=handler)

        client = create_async_client(user_agent="custom-agent/9.9")
        try:
            await client.get("https://example.com/api")
        finally:
            await client.aclose()

    assert captured["ua"] == "custom-agent/9.9"
