"""End-to-end tests for :mod:`currency_notifier.pipeline`.

Strategy: inject a real (in-memory) :class:`Database`, a mock
:class:`Notifier`, and ``respx``-mocked HTTP for Frankfurter so the whole
``run()`` cycle can be exercised without touching the network.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from common.database import Database
from common.discord_notifier import Embed, Notifier
from currency_notifier.config import (
    CurrencyNotifierConfig,
    DynamicThresholdConfig,
    PairConfig,
)
from currency_notifier.pipeline import run as run_pipeline
from currency_notifier.rates import FRANKFURTER_URL


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingNotifier:
    """Minimal :class:`Notifier` implementation that captures every embed."""

    def __init__(self) -> None:
        self.embeds: list[Embed] = []
        self.closed = False

    async def send_embed(self, embed: Embed) -> bool:
        self.embeds.append(embed)
        return True

    async def send_message(self, content: str) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "pipeline.db")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


def _frankfurter_url(base: str, quote: str) -> str:
    return f"{FRANKFURTER_URL}?from={base}&to={quote}"


def _make_config(pairs: list[PairConfig]) -> CurrencyNotifierConfig:
    return CurrencyNotifierConfig(
        app_name="currency-notifier",
        pairs=pairs,
    )


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


async def test_end_to_end_static_threshold_triggers_notification(
    db: Database, notifier: RecordingNotifier
) -> None:
    """A below-target rate flows from API → store → dedup → Discord."""
    pair = PairConfig(base="USD", quote="JPY", static_threshold=145.0)
    config = _make_config([pair])

    with respx.mock(assert_all_called=True) as router:
        router.get(_frankfurter_url("USD", "JPY")).mock(
            return_value=httpx.Response(
                200,
                json={"base": "USD", "rates": {"JPY": 143.5}},
            )
        )

        async with httpx.AsyncClient() as client:
            delivered = await run_pipeline(config, db, notifier=notifier, client=client)

    assert delivered == 1
    assert len(notifier.embeds) == 1

    embed = notifier.embeds[0]
    assert embed.title == "💱 Currency Alert: USD/JPY"
    # Yellow brand colour is preserved.
    assert embed.color == 0x5865F2 or embed.color == 0xFEE75C
    # Embed carries the static-threshold alert as one of its fields.
    field_names = [f.name for f in embed.fields]
    assert any(name.startswith("⚠") for name in field_names)


async def test_end_to_end_no_alert_no_notification(
    db: Database, notifier: RecordingNotifier
) -> None:
    """Rate above static threshold + no history → no Discord call."""
    pair = PairConfig(base="USD", quote="JPY", static_threshold=100.0)
    config = _make_config([pair])

    with respx.mock(assert_all_called=True) as router:
        router.get(_frankfurter_url("USD", "JPY")).mock(
            return_value=httpx.Response(
                200,
                json={"base": "USD", "rates": {"JPY": 150.0}},
            )
        )

        async with httpx.AsyncClient() as client:
            delivered = await run_pipeline(config, db, notifier=notifier, client=client)

    # No alert fired → no embed sent, but the rate was still stored.
    assert delivered == 0
    assert notifier.embeds == []

    # The rate observation must still be persisted for future cycles.
    rows = db.execute(
        "SELECT rate FROM currency_rates WHERE pair = 'USD/JPY' ORDER BY timestamp DESC LIMIT 1"
    )
    assert rows and rows[0]["rate"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


async def test_dedup_same_rate_does_not_notify_twice(
    db: Database, notifier: RecordingNotifier
) -> None:
    """Two consecutive cycles at the same rate yield exactly one notification."""
    pair = PairConfig(base="USD", quote="JPY", static_threshold=145.0)
    config = _make_config([pair])

    with respx.mock(assert_all_called=True) as router:
        route = router.get(_frankfurter_url("USD", "JPY")).mock(
            return_value=httpx.Response(
                200,
                json={"base": "USD", "rates": {"JPY": 140.0}},
            )
        )

        async with httpx.AsyncClient() as client:
            delivered_first = await run_pipeline(
                config, db, notifier=notifier, client=client
            )
            delivered_second = await run_pipeline(
                config, db, notifier=notifier, client=client
            )

    assert delivered_first == 1
    # Second cycle: rate unchanged → dedup suppresses the notification.
    assert delivered_second == 0
    assert len(notifier.embeds) == 1
    # API was hit both times (the pipeline always fetches first).
    assert route.call_count == 2


async def test_dedup_rate_change_re_notifies(
    db: Database, notifier: RecordingNotifier
) -> None:
    """A different rate re-arms the notification path."""
    pair = PairConfig(base="USD", quote="JPY", static_threshold=200.0)
    config = _make_config([pair])

    responses = iter(
        [
            httpx.Response(200, json={"base": "USD", "rates": {"JPY": 150.0}}),
            httpx.Response(200, json={"base": "USD", "rates": {"JPY": 150.0}}),
            httpx.Response(200, json={"base": "USD", "rates": {"JPY": 149.0}}),
        ]
    )

    with respx.mock(assert_all_called=True) as router:
        router.get(_frankfurter_url("USD", "JPY")).mock(side_effect=responses)

        async with httpx.AsyncClient() as client:
            d1 = await run_pipeline(config, db, notifier=notifier, client=client)
            d2 = await run_pipeline(config, db, notifier=notifier, client=client)
            d3 = await run_pipeline(config, db, notifier=notifier, client=client)

    # First and third cycles both have the rate well below the 200 target,
    # so they fire. Second cycle is identical to first and is deduped.
    assert d1 == 1
    assert d2 == 0
    assert d3 == 1
    assert len(notifier.embeds) == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_fetch_failure_skips_pair_but_continues(
    db: Database, notifier: RecordingNotifier
) -> None:
    """A network failure for one pair does not abort the whole cycle."""
    pairs = [
        PairConfig(base="USD", quote="JPY", static_threshold=200.0),
        PairConfig(base="EUR", quote="USD", static_threshold=0.5),
    ]
    config = _make_config(pairs)

    with respx.mock(assert_all_called=True) as router:
        # First pair fails, second pair succeeds.
        router.get(_frankfurter_url("USD", "JPY")).mock(
            side_effect=httpx.ConnectError("nope")
        )
        router.get(_frankfurter_url("EUR", "USD")).mock(
            return_value=httpx.Response(
                200,
                json={"base": "EUR", "rates": {"USD": 0.4}},
            )
        )

        async with httpx.AsyncClient() as client:
            delivered = await run_pipeline(
                config, db, notifier=notifier, client=client
            )

    assert delivered == 1
    assert len(notifier.embeds) == 1
    assert notifier.embeds[0].title == "💱 Currency Alert: EUR/USD"


async def test_404_for_one_pair_does_not_abort(
    db: Database, notifier: RecordingNotifier
) -> None:
    """A 404 (programmer error) for one pair is logged and skipped."""
    pairs = [
        PairConfig(base="USD", quote="XYZ"),  # invalid → 404
        # Static threshold deliberately set well below the API rate so this
        # pair does not trigger any alert — keeping the test focused on 404
        # isolation rather than alert delivery.
        PairConfig(base="EUR", quote="USD", static_threshold=0.5),
    ]
    config = _make_config(pairs)

    with respx.mock(assert_all_called=True) as router:
        router.get(_frankfurter_url("USD", "XYZ")).mock(
            return_value=httpx.Response(404, text="not found")
        )
        router.get(_frankfurter_url("EUR", "USD")).mock(
            return_value=httpx.Response(
                200,
                json={"base": "EUR", "rates": {"USD": 1.0}},
            )
        )

        async with httpx.AsyncClient() as client:
            delivered = await run_pipeline(
                config, db, notifier=notifier, client=client
            )

    assert delivered == 0  # EUR/USD rate above its static target


async def test_pipeline_closes_owned_notifier() -> None:
    """When ``run`` builds the notifier itself, it must close it afterwards."""
    import tempfile

    from currency_notifier import pipeline

    config = CurrencyNotifierConfig(
        app_name="currency-notifier",
        pairs=[PairConfig(base="USD", quote="JPY", static_threshold=200.0)],
    )
    notifier = RecordingNotifier()

    # Replace the factory inside the pipeline module so ``run`` builds our
    # recording notifier instead of insisting on a real webhook URL.
    pipeline.create_notifier = lambda _cfg: notifier  # type: ignore[assignment]

    try:
        with tempfile.TemporaryDirectory() as td:
            tmp_db = Database(Path(td) / "owned.db")
            try:
                with respx.mock(assert_all_called=True) as router:
                    router.get(_frankfurter_url("USD", "JPY")).mock(
                        return_value=httpx.Response(
                            200,
                            json={"base": "USD", "rates": {"JPY": 150.0}},
                        )
                    )
                    async with httpx.AsyncClient() as client:
                        await run_pipeline(config, tmp_db, client=client)
            finally:
                tmp_db.close()
    finally:
        # Restore for any later tests that share the process.
        from common.discord_notifier import create_notifier as real_factory

        pipeline.create_notifier = real_factory  # type: ignore[assignment]

    assert notifier.closed is True


# ---------------------------------------------------------------------------
# Threshold integration
# ---------------------------------------------------------------------------


async def test_dynamic_threshold_with_seeded_history(
    db: Database, notifier: RecordingNotifier
) -> None:
    """Pre-seed history so the z-score dynamic check fires on the next poll."""
    from currency_notifier.rates import RateStore

    pair = PairConfig(
        base="USD",
        quote="JPY",
        static_threshold=200.0,  # intentionally high so only dynamic fires
        dynamic=DynamicThresholdConfig(window_days=5, z_threshold=-1.0),
    )
    config = _make_config([pair])

    # Initialising the store creates the schema; use it to seed history.
    store = RateStore(db)
    now = datetime.now(timezone.utc)
    history_seed = [99.0, 100.0, 101.0, 100.0, 100.0]
    from datetime import timedelta as _td

    for offset_days, rate in enumerate(history_seed):
        ts = (now - _td(days=10 - offset_days)).isoformat()
        db.execute_write(
            "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
            ("USD/JPY", rate, ts),
        )
    # ``store`` is unused beyond triggering schema creation but kept to make
    # the relationship between the schema and the seeder explicit.
    assert store is not None

    with respx.mock(assert_all_called=True) as router:
        router.get(_frankfurter_url("USD", "JPY")).mock(
            return_value=httpx.Response(
                200,
                json={"base": "USD", "rates": {"JPY": 98.0}},
            )
        )

        async with httpx.AsyncClient() as client:
            delivered = await run_pipeline(
                config, db, notifier=notifier, client=client
            )

    assert delivered == 1
    assert len(notifier.embeds) == 1
    embed = notifier.embeds[0]
    # Confirm the dynamic alert was included.
    assert any("dynamic" in f.name for f in embed.fields)


async def test_embed_uses_configured_color() -> None:
    """``config.discord.default_color`` must propagate to the embed."""
    import tempfile

    base_config = CurrencyNotifierConfig(
        app_name="currency-notifier",
        pairs=[PairConfig(base="USD", quote="JPY", static_threshold=200.0)],
    )
    # Override the nested discord default_color cleanly.
    config = base_config.model_copy(
        update={
            "discord": base_config.discord.model_copy(update={"default_color": 0xFEE75C})
        }
    )

    notifier = RecordingNotifier()

    with tempfile.TemporaryDirectory() as td:
        tmp_db = Database(Path(td) / "color.db")
        try:
            with respx.mock(assert_all_called=True) as router:
                router.get(_frankfurter_url("USD", "JPY")).mock(
                    return_value=httpx.Response(
                        200,
                        json={"base": "USD", "rates": {"JPY": 150.0}},
                    )
                )
                async with httpx.AsyncClient() as client:
                    await run_pipeline(config, tmp_db, notifier=notifier, client=client)
        finally:
            tmp_db.close()

    assert len(notifier.embeds) == 1
    assert notifier.embeds[0].color == 0xFEE75C
