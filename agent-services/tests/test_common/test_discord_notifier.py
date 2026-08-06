"""Tests for :mod:`common.discord_notifier`.

These tests use ``respx`` to mock ``httpx`` calls and an autouse fixture to
disable ``asyncio.sleep`` so retry backoff does not actually sleep in tests
(production waits are 1s, 3s, … between attempts).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from common.discord_notifier import (
    DEFAULT_EMBED_COLOR,
    BotNotifier,
    Embed,
    EmbedField,
    WebhookNotifier,
    create_notifier,
    embed_to_dict,
)
from common.exceptions import NotifyError

WEBHOOK_URL = "https://discord.com/api/webhooks/1234567890/abcdefghij"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tenacity's inter-attempt backoff instantaneous in tests.

    Production waits are 1s, 3s, … — far too slow for a unit-test suite.
    Patching :func:`asyncio.sleep` short-circuits the sleeps while keeping
    every other piece of retry behaviour intact.
    """

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)


def _fast_retryer() -> AsyncRetrying:
    """A retry policy that uses the same exception rules as production but
    with zero-wait backoff — convenient for tests that don't care about
    timing but want real tenacity iteration."""
    return AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.0, exp_base=1.0, min=0.0, max=0.0),
        retry=retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.ConnectError, httpx.ConnectTimeout)
        ),
        reraise=False,
    )


def _sample_embed() -> Embed:
    return Embed(
        title="ETFs in focus",
        description="Daily ETF signal summary",
        color=0x00FF00,
        url="https://example.com/etf",
        timestamp="2026-08-06T00:00:00Z",
        footer="agent-services",
        fields=[
            EmbedField(name="Ticker", value="VOO", inline=True),
            EmbedField(name="Signal", value="BUY", inline=True),
            EmbedField(name="Confidence", value="0.82", inline=False),
        ],
    )


# ---------------------------------------------------------------------------
# embed_to_dict
# ---------------------------------------------------------------------------


class TestEmbedToDict:
    """Direct tests for the embed serializer (no network, no retries)."""

    def test_minimal_embed_only_has_title_and_default_color(self) -> None:
        # The default color (Blurple) is always included so Discord does not
        # pick its own theme — predictable branding.
        payload = embed_to_dict(Embed(title="hello"))
        assert payload == {"title": "hello", "color": DEFAULT_EMBED_COLOR}

    def test_default_color_is_blurple(self) -> None:
        payload = embed_to_dict(Embed(title="x"))
        assert payload["color"] == DEFAULT_EMBED_COLOR
        assert payload["color"] == 0x5865F2

    def test_full_embed_round_trip(self) -> None:
        embed = _sample_embed()
        payload = embed_to_dict(embed)

        assert payload["title"] == "ETFs in focus"
        assert payload["description"] == "Daily ETF signal summary"
        assert payload["color"] == 0x00FF00
        assert payload["url"] == "https://example.com/etf"
        assert payload["timestamp"] == "2026-08-06T00:00:00Z"

        # Footer must be an object with a ``text`` key (Discord API shape).
        assert payload["footer"] == {"text": "agent-services"}

        # Fields must preserve ordering and structure.
        assert payload["fields"] == [
            {"name": "Ticker", "value": "VOO", "inline": True},
            {"name": "Signal", "value": "BUY", "inline": True},
            {"name": "Confidence", "value": "0.82", "inline": False},
        ]

    def test_empty_optional_fields_are_omitted(self) -> None:
        payload = embed_to_dict(Embed(title="t", description="", footer=None))
        assert "description" not in payload
        assert "footer" not in payload
        assert "fields" not in payload
        assert "url" not in payload
        assert "timestamp" not in payload

    def test_color_is_coerced_to_int(self) -> None:
        payload = embed_to_dict(Embed(title="x", color=0xFFAA00))
        assert payload["color"] == 0xFFAA00
        assert isinstance(payload["color"], int)

    def test_embed_field_default_inline_false(self) -> None:
        field = EmbedField(name="k", value="v")
        assert field.inline is False


# ---------------------------------------------------------------------------
# WebhookNotifier
# ---------------------------------------------------------------------------


class TestWebhookNotifier:
    """Tests for the async webhook delivery path."""

    @pytest.mark.asyncio
    async def test_send_embed_posts_correct_url_and_payload(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            route = router.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(204, text="")
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_embed(_sample_embed())

            assert ok is True
            assert route.call_count == 1

            # Inspect the actual JSON that went over the wire.
            request = route.calls.last.request
            body: dict[str, Any] = request.content.decode()  # type: ignore[assignment]
            # ``request.content`` is bytes; decode and re-parse for clarity.
            import json as _json

            body = _json.loads(request.content)  # type: ignore[arg-type]
            assert "embeds" in body
            assert isinstance(body["embeds"], list)
            assert len(body["embeds"]) == 1

            embed = body["embeds"][0]
            assert embed["title"] == "ETFs in focus"
            assert embed["color"] == 0x00FF00
            assert len(embed["fields"]) == 3
            assert embed["footer"] == {"text": "agent-services"}

    @pytest.mark.asyncio
    async def test_send_message_posts_content(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            route = router.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(204, text="")
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_message("hello world")

            assert ok is True
            assert route.call_count == 1

            import json as _json

            body = _json.loads(route.calls.last.request.content)  # type: ignore[arg-type]
            assert body == {"content": "hello world"}

    @pytest.mark.asyncio
    async def test_empty_content_skips_request(self) -> None:
        with respx.mock(assert_all_called=False) as router:
            route = router.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(204, text="")
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_message("")

            assert ok is False
            assert route.call_count == 0  # never reached the wire

    @pytest.mark.asyncio
    async def test_retries_on_5xx_then_succeeds(self) -> None:
        """503 twice then 204 → exactly 3 attempts, returns True."""
        with respx.mock() as router:
            route = router.post(WEBHOOK_URL).mock(
                side_effect=[
                    httpx.Response(503, text="overloaded"),
                    httpx.Response(503, text="overloaded"),
                    httpx.Response(204, text=""),
                ]
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_message("retry me")

            assert ok is True
            assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_5xx_then_final_failure_returns_false(self) -> None:
        """503 three times → 3 attempts, returns False, never raises."""
        with respx.mock() as router:
            route = router.post(WEBHOOK_URL).mock(
                side_effect=[
                    httpx.Response(503, text="down"),
                    httpx.Response(503, text="down"),
                    httpx.Response(503, text="down"),
                ]
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                # The bulletproof contract: NO exception escapes.
                ok = await notifier.send_message("will fail")

            assert ok is False
            assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_connect_error(self) -> None:
        """ConnectError on every attempt → 3 attempts, returns False."""
        with respx.mock() as router:
            route = router.post(WEBHOOK_URL).mock(
                side_effect=httpx.ConnectError("kaboom")
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_message("network down")

            assert ok is False
            assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_on_4xx(self) -> None:
        """400 is permanent: one attempt only, returns False."""
        with respx.mock() as router:
            route = router.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(400, text="bad payload")
            )

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_message("nope")

            assert ok is False
            assert route.call_count == 1  # NOT retried

    @pytest.mark.asyncio
    async def test_unexpected_exception_does_not_propagate(self) -> None:
        """Any exception type from inside an attempt must be swallowed."""

        class WeirdError(Exception):
            pass

        with respx.mock() as router:
            route = router.post(WEBHOOK_URL).mock(side_effect=WeirdError("oops"))

            async with WebhookNotifier(WEBHOOK_URL) as notifier:
                ok = await notifier.send_message("kaboom")

            assert ok is False
            # Tenacity should retry this too because it's caught by the broad
            # Exception handler in _deliver and counted as a failed attempt.
            assert route.call_count >= 1

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        notifier = WebhookNotifier(WEBHOOK_URL)
        await notifier.close()
        # A second close on the already-closed client should not raise.
        await notifier.close()

    @pytest.mark.asyncio
    async def test_custom_retryer_is_respected(self) -> None:
        """Passing a custom retryer must override the default policy."""
        with respx.mock() as router:
            route = router.post(WEBHOOK_URL).mock(
                side_effect=httpx.ConnectError("nope")
            )

            # One-attempt retryer: only 1 call, never retries.
            one_shot = AsyncRetrying(
                stop=stop_after_attempt(1),
                wait=wait_exponential(multiplier=0.0, exp_base=1.0, min=0.0, max=0.0),
                retry=retry_if_exception_type(
                    (httpx.HTTPStatusError, httpx.ConnectError)
                ),
                reraise=False,
            )

            async with WebhookNotifier(WEBHOOK_URL, retryer=one_shot) as notifier:
                ok = await notifier.send_message("x")

            assert ok is False
            assert route.call_count == 1


# ---------------------------------------------------------------------------
# BotNotifier (stub)
# ---------------------------------------------------------------------------


class TestBotNotifierStub:
    @pytest.mark.asyncio
    async def test_send_message_raises_not_implemented(self) -> None:
        notifier = BotNotifier(bot_token="tok", channel_id=1)
        with pytest.raises(NotImplementedError) as excinfo:
            await notifier.send_message("hello")
        assert "webhook mode" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_send_embed_raises_not_implemented(self) -> None:
        notifier = BotNotifier(bot_token="tok", channel_id=1)
        with pytest.raises(NotImplementedError) as excinfo:
            await notifier.send_embed(Embed(title="x"))
        assert "webhook mode" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_close_is_noop(self) -> None:
        notifier = BotNotifier(bot_token="tok", channel_id=1)
        await notifier.close()  # must not raise


# ---------------------------------------------------------------------------
# create_notifier factory
# ---------------------------------------------------------------------------


class TestCreateNotifier:
    def test_webhook_mode_with_dict_returns_webhook_notifier(self) -> None:
        notifier = create_notifier(
            {"mode": "webhook", "webhook_url": WEBHOOK_URL}
        )
        assert isinstance(notifier, WebhookNotifier)

    def test_bot_mode_with_dict_returns_bot_notifier(self) -> None:
        notifier = create_notifier(
            {"mode": "bot", "bot_token": "tok", "channel_id": 12345}
        )
        assert isinstance(notifier, BotNotifier)

    def test_webhook_mode_with_pydantic_model(self) -> None:
        class Cfg(BaseModel):
            mode: str
            webhook_url: str | None = None
            bot_token: str | None = None
            channel_id: int | None = None

        notifier = create_notifier(
            Cfg(mode="webhook", webhook_url=WEBHOOK_URL)
        )
        assert isinstance(notifier, WebhookNotifier)

    def test_bot_mode_with_pydantic_model(self) -> None:
        class Cfg(BaseModel):
            mode: str
            webhook_url: str | None = None
            bot_token: str | None = None
            channel_id: int | None = None

        notifier = create_notifier(
            Cfg(mode="bot", bot_token="tok", channel_id=999)
        )
        assert isinstance(notifier, BotNotifier)

    def test_missing_webhook_url_raises_notify_error(self) -> None:
        with pytest.raises(NotifyError) as excinfo:
            create_notifier({"mode": "webhook", "webhook_url": ""})
        assert "webhook_url" in str(excinfo.value)

    def test_missing_bot_token_raises_notify_error(self) -> None:
        with pytest.raises(NotifyError):
            create_notifier({"mode": "bot", "bot_token": "", "channel_id": 1})

    def test_missing_channel_id_raises_notify_error(self) -> None:
        with pytest.raises(NotifyError):
            create_notifier({"mode": "bot", "bot_token": "tok", "channel_id": 0})

    def test_unknown_mode_raises_notify_error(self) -> None:
        with pytest.raises(NotifyError) as excinfo:
            create_notifier({"mode": "carrier-pigeon"})
        assert "carrier-pigeon" in str(excinfo.value)

    def test_missing_mode_raises_notify_error(self) -> None:
        with pytest.raises(NotifyError):
            create_notifier({})

    def test_direct_constructor_rejects_empty_url(self) -> None:
        """Defence-in-depth: WebhookNotifier() also rejects empty URL."""
        with pytest.raises(NotifyError):
            WebhookNotifier("")


# ---------------------------------------------------------------------------
# Logger behaviour — ensure errors are logged, not raised
# ---------------------------------------------------------------------------


class TestLoggingBehaviour:
    @pytest.mark.asyncio
    async def test_5xx_failure_is_logged(self) -> None:
        """Final failure must emit an error log, not propagate."""
        with respx.mock() as router:
            router.post(WEBHOOK_URL).mock(
                side_effect=[
                    httpx.Response(503, text="x"),
                    httpx.Response(503, text="x"),
                    httpx.Response(503, text="x"),
                ]
            )

            with patch("common.discord_notifier.logger") as mock_logger:
                mock_logger.error = MagicMock()
                async with WebhookNotifier(WEBHOOK_URL) as notifier:
                    ok = await notifier.send_message("hi")

            assert ok is False
            # At least one logger.error call should have happened.
            assert mock_logger.error.call_count >= 1
