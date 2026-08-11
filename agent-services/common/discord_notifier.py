"""Discord notification primitives: webhook + (stub) bot.

This module exposes:

* :class:`EmbedField` / :class:`Embed` — plain dataclasses describing a Discord
  rich embed.
* :func:`embed_to_dict` — convert an :class:`Embed` to a Discord API payload.
* :class:`WebhookNotifier` — async webhook delivery via ``httpx``, with
  bulletproof retries (never raises to the caller).
* :class:`BotNotifier` — stub for future bot-mode implementation.
* :func:`create_notifier` — factory that builds the right notifier from
  configuration.

Design contract
---------------
``WebhookNotifier.send_message`` and ``WebhookNotifier.send_embed`` are
**bulletproof**: they never raise to the caller. Per-call failures that
exhaust retries are logged and surface as ``False``. Configuration errors
(missing webhook URL, unknown mode, …) raise :class:`NotifyError` from
``create_notifier`` instead — those are programmer errors, not delivery
problems.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from common.exceptions import NotifyError

logger = logging.getLogger(__name__)

# Default Discord brand color (Blurple). Kept as a module-level constant so
# tests can reference it without re-deriving.
DEFAULT_EMBED_COLOR = 0x5865F2


class _PermanentRejectError(Exception):
    """Raised by ``WebhookNotifier`` for 4xx responses.

    4xx is a permanent failure (bad payload, missing webhook, auth issue) and
    must not trigger retries. We use a non-:class:`httpx.HTTPStatusError`
    subclass so tenacity's ``retry_if_exception_type`` predicate does not
    accidentally match it — only true 5xx HTTPStatusErrors and connection
    errors are retried.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"Discord rejected payload with HTTP {status_code}: {body[:200]!r}"
        )


# ---------------------------------------------------------------------------
# Embed dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EmbedField:
    """A single field inside a Discord embed.

    Discord embeds are limited to 25 fields; we do not enforce that here —
    Discord itself will reject the payload with a 400 if exceeded, which the
    notifier treats as a delivery failure.
    """

    name: str
    value: str
    inline: bool = False


@dataclass
class Embed:
    """A Discord rich embed.

    All fields are optional except ``title``. ``fields`` defaults to an empty
    list (not shared mutable default) and any ``None`` / empty string value
    is omitted from the JSON payload produced by :func:`embed_to_dict`.
    """

    title: str
    description: str = ""
    color: int = DEFAULT_EMBED_COLOR
    fields: list[EmbedField] = field(default_factory=list)
    url: str | None = None
    timestamp: str | None = None  # ISO-8601 string
    footer: str | None = None
    image: str | None = None  # URL or local path reference


def embed_to_dict(embed: Embed) -> dict[str, Any]:
    """Serialize an :class:`Embed` to a Discord API payload fragment.

    ``None`` and empty-string fields are omitted so the payload only carries
    data Discord actually expects to see.
    """
    payload: dict[str, Any] = {"title": embed.title}

    if embed.description:
        payload["description"] = embed.description
    if embed.color is not None:
        payload["color"] = int(embed.color)
    if embed.url:
        payload["url"] = embed.url
    if embed.timestamp:
        payload["timestamp"] = embed.timestamp
    if embed.footer:
        # Discord's footer is an object: ``{"text": "..."}``.
        payload["footer"] = {"text": embed.footer}
    if embed.image:
        payload["image"] = {"url": embed.image}
    if embed.fields:
        payload["fields"] = [
            {"name": f.name, "value": f.value, "inline": f.inline}
            for f in embed.fields
        ]

    return payload


# ---------------------------------------------------------------------------
# Notifier interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """Structural type for any notifier implementation."""

    async def send_message(self, content: str) -> bool: ...
    async def send_embed(self, embed: Embed) -> bool: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Webhook notifier
# ---------------------------------------------------------------------------


def _default_retrying() -> AsyncRetrying:
    """Build the default production retry policy.

    Three total attempts with exponential backoff (1s, 3s, 9s schedule). Only
    retryable transport / server failures are retried; 4xx is treated as a
    programming error and propagated immediately.
    """
    return AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, exp_base=3, min=0.0, max=30.0),
        retry=retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.ConnectError, httpx.ConnectTimeout)
        ),
        reraise=False,
    )


class WebhookNotifier:
    """Deliver Discord messages via a webhook URL with bulletproof retries.

    The instance owns its own :class:`httpx.AsyncClient` and must be closed
    via :meth:`close` (or used as an async context manager) to release the
    underlying connection pool.
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: float = 10.0,
        retryer: AsyncRetrying | None = None,
    ) -> None:
        if not webhook_url:
            # Defence in depth — create_notifier should have caught this.
            raise NotifyError("WebhookNotifier requires a non-empty webhook_url")

        self._webhook_url = webhook_url
        self._timeout = timeout
        self._retryer = retryer or _default_retrying()
        self._client = httpx.AsyncClient(timeout=timeout)

    # -- Context manager support -------------------------------------------

    async def __aenter__(self) -> WebhookNotifier:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # -- Public API --------------------------------------------------------

    async def send_message(self, content: str) -> bool:
        """Send a plain-text message via the webhook.

        Returns ``True`` on success, ``False`` after retry exhaustion or any
        unexpected exception. Never raises to the caller.
        """
        if not content:
            logger.warning("send_message called with empty content; skipping")
            return False
        return await self._deliver({"content": content})

    async def send_embed(self, embed: Embed) -> bool:
        """Send a rich embed via the webhook.

        Returns ``True`` on success, ``False`` after retry exhaustion or any
        unexpected exception. Never raises to the caller.
        """
        return await self._deliver({"embeds": [embed_to_dict(embed)]})

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()

    # -- Internals ---------------------------------------------------------

    async def _deliver(self, payload: dict[str, Any]) -> bool:
        """Run one logical send, wrapped in tenacity retries.

        Tenacity only catches :class:`httpx.HTTPStatusError` (5xx only — 4xx
        is converted to :class:`_PermanentRejectError`, which tenacity does
        not retry) and connection errors. Any other exception, plus
        :class:`tenacity.RetryError`, is caught here and converted to
        ``False``.
        """
        try:
            async for attempt in self._retryer:
                with attempt:
                    await self._attempt(payload)
        except _PermanentRejectError as exc:
            logger.error(
                "Discord webhook rejected payload with HTTP %s: %s",
                exc.status_code,
                exc.body,
            )
            return False
        except RetryError as exc:
            logger.error(
                "Discord webhook delivery failed after retries: %s",
                exc,
                exc_info=True,
            )
            return False
        except Exception as exc:  # noqa: BLE001 — bulletproof contract
            logger.error(
                "Discord webhook delivery raised unexpected exception: %s",
                exc,
                exc_info=True,
            )
            return False
        return True

    async def _attempt(self, payload: dict[str, Any]) -> None:
        """Single HTTP attempt.

        * 2xx / 3xx → returns successfully (Discord returns 204 on webhook send).
        * 4xx → raises :class:`_PermanentRejectError` (tenacity will NOT retry).
        * 5xx → raises :class:`httpx.HTTPStatusError` (tenacity WILL retry).
        """
        response = await self._client.post(self._webhook_url, json=payload)
        if 400 <= response.status_code < 500:
            raise _PermanentRejectError(response.status_code, response.text)
        # raise_for_status() raises HTTPStatusError for any >= 400 status,
        # so we only call it for 5xx (4xx already handled above).
        if response.status_code >= 500:
            response.raise_for_status()


# ---------------------------------------------------------------------------
# Bot notifier (stub)
# ---------------------------------------------------------------------------


class BotNotifier:
    """Stub for Discord bot-mode delivery.

    Bot mode requires a long-running gateway connection (typically via
    ``discord.py``), which does not fit the on-demand, stateless style of
    this module. Until we wire up a real implementation, every send method
    raises :class:`NotImplementedError` so callers fail loudly during
    development. :meth:`close` is a no-op because there are no resources to
    release.
    """

    _STUB_MESSAGE = (
        "Discord bot mode not yet implemented. Use webhook mode."
    )

    def __init__(self, bot_token: str, channel_id: int) -> None:
        # We accept the parameters so the constructor matches the eventual
        # real signature, but we do nothing with them yet.
        self._bot_token = bot_token
        self._channel_id = channel_id

    async def send_message(self, content: str) -> bool:
        raise NotImplementedError(self._STUB_MESSAGE)

    async def send_embed(self, embed: Embed) -> bool:
        raise NotImplementedError(self._STUB_MESSAGE)

    async def close(self) -> None:
        # No-op: there are no resources to release in the stub.
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from either a dict or a pydantic-style object."""
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def create_notifier(config: Any) -> Notifier:
    """Build a notifier from configuration.

    ``config`` may be either a ``dict`` or a pydantic model (anything with
    attribute access). Recognised keys: ``mode`` (``"webhook"`` or ``"bot"``),
    ``webhook_url``, ``bot_token``, ``channel_id``.

    Raises :class:`NotifyError` if the configuration is invalid.
    """
    mode = _config_get(config, "mode")
    webhook_url = _config_get(config, "webhook_url")
    bot_token = _config_get(config, "bot_token")
    channel_id = _config_get(config, "channel_id")

    if mode == "webhook":
        if not webhook_url:
            raise NotifyError(
                "create_notifier: mode='webhook' requires a non-empty 'webhook_url'"
            )
        return WebhookNotifier(webhook_url)

    if mode == "bot":
        if not bot_token or not channel_id:
            raise NotifyError(
                "create_notifier: mode='bot' requires 'bot_token' and 'channel_id'"
            )
        return BotNotifier(bot_token, channel_id)

    raise NotifyError(
        f"create_notifier: unknown mode {mode!r}; expected 'webhook' or 'bot'"
    )


__all__ = [
    "DEFAULT_EMBED_COLOR",
    "Embed",
    "EmbedField",
    "Notifier",
    "WebhookNotifier",
    "BotNotifier",
    "NotifyError",
    "create_notifier",
    "embed_to_dict",
]
