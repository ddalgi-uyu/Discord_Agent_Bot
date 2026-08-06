"""Tests for :mod:`currency_notifier.rates`."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from common.database import Database
from common.exceptions import FetchError
from currency_notifier.rates import (
    FRANKFURTER_URL,
    RateStore,
    fetch_rate,
)


# ---------------------------------------------------------------------------
# fetch_rate
# ---------------------------------------------------------------------------


async def test_fetch_rate_returns_parsed_value() -> None:
    """A 200 JSON response with the right shape returns the rate as float."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            return_value=httpx.Response(
                200,
                json={
                    "amount": 1.0,
                    "base": "USD",
                    "date": "2026-08-06",
                    "rates": {"JPY": 152.34},
                },
            )
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate == pytest.approx(152.34)
    assert isinstance(rate, float)


async def test_fetch_rate_uppercases_currency_codes_in_url() -> None:
    """Lowercase inputs are normalised before building the URL."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{FRANKFURTER_URL}?from=EUR&to=USD").mock(
            return_value=httpx.Response(
                200, json={"rates": {"USD": 1.08}}
            )
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("eur", "usd", client)

    assert rate == pytest.approx(1.08)
    # Verify the request actually used uppercase codes.
    assert route.call_count == 1
    request = route.calls.last.request
    assert "from=EUR" in str(request.url)
    assert "to=USD" in str(request.url)


async def test_fetch_rate_404_raises_fetch_error() -> None:
    """HTTP 404 means unknown pair — must surface as FetchError, not None."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=XYZ").mock(
            return_value=httpx.Response(404, text="not found")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(FetchError, match="404"):
                await fetch_rate("USD", "XYZ", client)


async def test_fetch_rate_5xx_returns_none_and_does_not_raise() -> None:
    """5xx errors are transient and must surface as ``None``, not raise."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            return_value=httpx.Response(503, text="upstream down")
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate is None


async def test_fetch_rate_connect_error_returns_none() -> None:
    """Network errors are logged and reported as ``None``."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            side_effect=httpx.ConnectError("boom")
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate is None


async def test_fetch_rate_read_timeout_returns_none() -> None:
    """Read timeouts are logged and reported as ``None``."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            side_effect=httpx.ReadTimeout("slow")
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate is None


async def test_fetch_rate_malformed_json_returns_none() -> None:
    """Non-JSON bodies are logged and reported as ``None``."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            return_value=httpx.Response(200, text="not json")
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate is None


async def test_fetch_rate_missing_quote_returns_none() -> None:
    """A well-formed payload without the requested quote returns ``None``."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            return_value=httpx.Response(
                200,
                json={
                    "base": "USD",
                    "rates": {"EUR": 0.92},  # JPY missing
                },
            )
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate is None


async def test_fetch_rate_non_numeric_returns_none() -> None:
    """A non-numeric ``rates[quote]`` value returns ``None``."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{FRANKFURTER_URL}?from=USD&to=JPY").mock(
            return_value=httpx.Response(
                200,
                json={"base": "USD", "rates": {"JPY": "unavailable"}},
            )
        )
        async with httpx.AsyncClient() as client:
            rate = await fetch_rate("USD", "JPY", client)

    assert rate is None


# ---------------------------------------------------------------------------
# RateStore — round-trip / dedup
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "rates.db")
    try:
        yield database
    finally:
        database.close()


async def test_store_then_get_last_returns_same_rate(db: Database) -> None:
    store = RateStore(db)
    await store.store_rate("USD/JPY", 150.123)
    last = await store.get_last_rate("USD/JPY")
    assert last == pytest.approx(150.123)


async def test_get_last_returns_none_when_empty(db: Database) -> None:
    store = RateStore(db)
    assert await store.get_last_rate("USD/JPY") is None


async def test_get_last_returns_most_recent(db: Database) -> None:
    store = RateStore(db)
    # Insert two rows directly with explicit timestamps so order is unambiguous.
    db.execute_write(
        "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
        ("USD/JPY", 100.0, "2026-01-01T00:00:00+00:00"),
    )
    db.execute_write(
        "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
        ("USD/JPY", 200.0, "2026-08-06T00:00:00+00:00"),
    )

    assert await store.get_last_rate("USD/JPY") == pytest.approx(200.0)


async def test_get_recent_returns_chronological_within_window(db: Database) -> None:
    store = RateStore(db)
    now = datetime.now(timezone.utc)
    db.execute_write(
        "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
        ("USD/JPY", 100.0, (now - timedelta(days=10)).isoformat()),
    )
    db.execute_write(
        "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
        ("USD/JPY", 110.0, (now - timedelta(days=3)).isoformat()),
    )
    db.execute_write(
        "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
        ("USD/JPY", 120.0, (now - timedelta(days=1)).isoformat()),
    )
    # Outside the 5-day window — must be excluded.
    db.execute_write(
        "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
        ("USD/JPY", 999.0, (now - timedelta(days=20)).isoformat()),
    )

    rows = await store.get_recent_rates("USD/JPY", days=5)

    assert [r for r, _ts in rows] == [pytest.approx(110.0), pytest.approx(120.0)]
    # Verify ordering is ascending by timestamp.
    timestamps = [ts for _r, ts in rows]
    assert timestamps == sorted(timestamps)


async def test_should_notify_true_when_first_observation(db: Database) -> None:
    """No prior data → first observation is always news."""
    store = RateStore(db)
    assert await store.should_notify("USD/JPY", 150.0) is True


async def test_should_notify_false_when_unchanged(db: Database) -> None:
    """Storing then asking again with the same rate must suppress."""
    store = RateStore(db)
    await store.store_rate("USD/JPY", 150.0)
    assert await store.should_notify("USD/JPY", 150.0) is False


async def test_should_notify_true_when_rate_changes(db: Database) -> None:
    """A change in rate triggers a new notification."""
    store = RateStore(db)
    await store.store_rate("USD/JPY", 150.0)
    assert await store.should_notify("USD/JPY", 149.5) is True


async def test_should_notify_per_pair_independent(db: Database) -> None:
    """Dedup state is per pair — JPY noise does not suppress EUR alerts."""
    store = RateStore(db)
    await store.store_rate("USD/JPY", 150.0)
    await store.store_rate("EUR/USD", 1.08)
    # Same rate as last stored for JPY → suppress.
    assert await store.should_notify("USD/JPY", 150.0) is False
    # First observation for USD/EUR (a third pair) → notify.
    assert await store.should_notify("USD/EUR", 0.92) is True
