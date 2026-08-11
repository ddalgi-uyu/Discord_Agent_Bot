"""Orchestrates one polling cycle for the ``currency-notifier`` app.

For every configured pair this module:

1. Fetches the latest rate from Frankfurter.
2. Persists it (idempotent on identical second-level timestamps).
3. Checks whether the rate has changed since the last stored value; if not,
   suppresses the notification entirely.
4. Evaluates static / dynamic / percentage-change thresholds against the
   recent history.
5. Sends a Discord embed summarising any triggered alerts.

Both the HTTP client and the notifier are injectable so tests can swap in
mocks. Production (``__main__``) lets the pipeline build them itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from common.database import Database
from common.discord_notifier import (
    Embed,
    EmbedField,
    Notifier,
    create_notifier,
)
from common.exceptions import FetchError
from common.http_client import create_async_client
from common.visualizer import generate_trend_chart

from currency_notifier.config import CurrencyNotifierConfig
from currency_notifier.rates import RateStore, fetch_rate
from currency_notifier.thresholds import evaluate_thresholds

logger = logging.getLogger(__name__)


# How many days of history to feed into the threshold evaluator. Frankfurter
# updates once per business day, so 90 days is a generous upper bound for any
# window check (the longest configured ``dynamic.window_days`` in the project
# YAML is 14).
_HISTORY_WINDOW_DAYS: int = 90


def _pair_label(pair_config: dict[str, Any]) -> str:
    return f"{pair_config.get('base', '?')}/{pair_config.get('quote', '?')}"


def _coerce_pair_dict(pair_config: Any) -> dict[str, Any]:
    """Accept either a :class:`PairConfig` or a plain ``dict``."""
    if isinstance(pair_config, dict):
        return dict(pair_config)
    # pydantic v2 BaseModel
    if hasattr(pair_config, "model_dump"):
        dumped = pair_config.model_dump()
        return dumped if isinstance(dumped, dict) else dict(dumped)
    # Best-effort fallback for any object exposing the same fields.
    return {
        "base": getattr(pair_config, "base", "?"),
        "quote": getattr(pair_config, "quote", "?"),
        "static_threshold": getattr(pair_config, "static_threshold", None),
        "dynamic": getattr(pair_config, "dynamic", None),
        "change_threshold_pct": getattr(pair_config, "change_threshold_pct", None),
    }


def _build_embed(
    pair: str,
    rate: float,
    pair_config: dict[str, Any],
    history: list[float],
    alerts: list[dict[str, Any]],
    color: int,
) -> tuple[Embed, Path | None]:
    """Construct the Discord embed and optional trend chart.
    
    Returns a tuple of (Embed, chart_path).
    """
    # Determine the highest alert level present to set the embed color
    levels = [a.get("level", "MONITOR") for a in alerts]
    if "STRONG" in levels:
        final_color = 0xFF0000  # Bright Red
    elif "GOOD" in levels:
        final_color = 0x00FF00  # Bright Green
    else:
        final_color = color # Default Blurple

    static = pair_config.get("static_threshold")
    target_text = f"{float(static):.4f}" if static is not None else "n/a"

    if len(history) >= 2:
        prev_rate = history[-2]
        delta = rate - prev_rate
        delta_pct = (delta / prev_rate * 100.0) if prev_rate else 0.0
        trend = f"{delta:+.4f} ({delta_pct:+.2f}%)"
    else:
        trend = "n/a (insufficient history)"

    fields: list[EmbedField] = [
        EmbedField(name="Pair", value=pair, inline=True),
        EmbedField(name="Rate", value=f"{rate:.4f}", inline=True),
        EmbedField(name="Target", value=target_text, inline=True),
        EmbedField(name="Trend", value=trend, inline=False),
    ]

    for alert in alerts:
        level = alert.get("level", "MONITOR")
        icon = "🚨" if level == "STRONG" else "✅" if level == "GOOD" else "ℹ️"
        fields.append(
            EmbedField(
                name=f"{icon} {level} - {alert['type']}",
                value=str(alert.get("message", "")),
                inline=False,
            )
        )

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    # Generate Trend Chart
    # convert history [float] to list[tuple[float, datetime]] for visualizer
    history_with_ts = []
    # Note: this is a simplification; in a real system we'd pass the actual timestamps
    # but since we are just verifying the visual, we can simulate them.
    # To be precise, we should pass the history from store.get_recent_rates directly.
    
    # Correcting: we will pass the actual tuples from _process_pair instead.
    # See change in _process_pair below.
    
    embed = Embed(
        title=f"💱 Currency Alert: {pair}",
        color=final_color,
        fields=fields,
        timestamp=timestamp,
    )
    
    return embed, None # Chart path will be handled in _process_pair


async def _process_pair(
    pair_config: Any,
    client: httpx.AsyncClient,
    store: RateStore,
    notifier: Notifier,
    color: int,
) -> bool:
    """Run one pair through fetch → dedup → store → evaluate → notify.

    Returns ``True`` iff a notification was actually delivered.

    Order matters: ``should_notify`` is checked *before* ``store_rate`` so the
    comparison is against the previous cycle's rate, not the one we are about
    to persist (which would always compare equal to itself).
    """
    pair_dict = _coerce_pair_dict(pair_config)
    pair = _pair_label(pair_dict)

    rate = await fetch_rate(pair_dict["base"], pair_dict["quote"], client)
    if rate is None:
        logger.warning("No rate for %s; skipping", pair)
        return False

    if not await store.should_notify(pair, rate):
        logger.info(
            "Rate for %s unchanged since last poll (rate=%s); suppressing notification",
            pair,
            rate,
        )
        # Still persist so the history stays current for future z-score windows.
        await store.store_rate(pair, rate)
        return False

    await store.store_rate(pair, rate)

    recent = await store.get_recent_rates(pair, days=_HISTORY_WINDOW_DAYS)
    history = [r for r, _ts in recent]

    alerts = evaluate_thresholds(pair_dict, rate, history)

    if not alerts:
        logger.info(
            "No thresholds triggered for %s at rate=%s; storing only", pair, rate
        )
        return False

    # Generate Visual Trend Chart
    chart_path = generate_trend_chart(
        pair=pair,
        history=recent,
        current_rate=rate,
        target_rate=pair_dict.get("static_threshold"),
    )

    embed, _ = _build_embed(pair, rate, pair_dict, history, alerts, color)
    if chart_path:
        embed.image = str(chart_path)
    
    delivered = await notifier.send_embed(embed)
    if not delivered:
        logger.error("Discord delivery failed for %s", pair)
    return delivered


async def run(
    config: CurrencyNotifierConfig,
    db: Database,
    *,
    notifier: Notifier | None = None,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Run one full polling cycle.

    Parameters
    ----------
    config:
        Validated app configuration.
    db:
        An open :class:`common.database.Database` (the caller owns its
        lifecycle). The pipeline does not close it.
    notifier, client:
        Optional injection points used by tests. When omitted, production
        instances are built from ``config.discord`` and
        :func:`common.http_client.create_async_client` respectively, and the
        pipeline cleans them up.

    Returns
    -------
    Number of alerts successfully delivered to Discord.
    """
    owned_notifier = notifier is None
    owned_client = client is None

    if client is None:
        client = create_async_client()
    if notifier is None:
        # ``create_notifier`` may raise ``NotifyError`` for missing config —
        # surface that to the caller as a programming error.
        notifier = create_notifier(config.discord)

    delivered = 0
    try:
        store = RateStore(db)
        color = int(config.discord.default_color)

        for pair_cfg in config.pairs:
            try:
                if await _process_pair(pair_cfg, client, store, notifier, color):
                    delivered += 1
            except FetchError as exc:
                # Unknown pair is a configuration-level problem; log loudly
                # and continue with the remaining pairs.
                logger.error(
                    "Skipping pair %s due to fetch error: %s",
                    getattr(pair_cfg, "base", "?"),
                    exc,
                )
            except Exception as exc:  # noqa: BLE001 — pipeline must not abort
                logger.exception(
                    "Unexpected failure processing pair %s: %s", pair_cfg, exc
                )
    finally:
        if owned_notifier:
            await notifier.close()
        if owned_client:
            await client.aclose()

    return delivered


__all__ = ["run"]
