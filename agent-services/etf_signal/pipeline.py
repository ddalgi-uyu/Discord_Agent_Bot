"""End-to-end ``etf-signal`` pipeline.

Orchestrates, for every symbol in the config:

1. :func:`etf_signal.indicators.compute_indicators` — yfinance + pandas_ta.
2. :func:`etf_signal.sentiment.analyze_sentiment` — FinBERT.
3. :func:`evaluate_signal` — RSI / Bollinger / SMA decision rules with a
   sentiment multiplier.
4. On a triggered signal: persist to SQLite (``common.database.Database``)
   and dispatch a green Discord embed via :func:`common.discord_notifier.create_notifier`.

The module exposes pure helpers (:func:`evaluate_signal`,
:func:`build_embed`, :func:`run`) so tests can exercise the decision logic
without touching yfinance, transformers, or Discord.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.database import Database
from common.discord_notifier import (
    Embed,
    EmbedField,
    Notifier,
    create_notifier,
)
from common.exceptions import NotifyError

from etf_signal.config import EtfSignalConfig
from etf_signal.indicators import compute_indicators
from etf_signal.sentiment import analyze_sentiment

logger = logging.getLogger(__name__)

# Default SQLite location. Overridable by callers / CLI via ``run(..., db_path=...)``.
DEFAULT_DB_PATH: Path = Path("data/etf_signal.db")

# Sentiment multiplier cut-offs. A score above ``SENTIMENT_BOOST`` is a
# bullish confirmation; below ``SENTIMENT_DELAY`` we suppress the
# notification ("delay").
SENTIMENT_BOOST: float = 0.2
SENTIMENT_DELAY: float = -0.2

# Tolerance for "price ≈ SMA200" in the moderate-buy rule (2%).
_SMA_NEAR_TOLERANCE: float = 0.02

# Discord embed title prefix for buy signals.
_BUY_TITLE_PREFIX: str = "📈 ETF BUY SIGNAL: "


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float``; return ``None`` on failure or ``None`` input."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # Treat NaN/inf as missing — they would corrupt downstream comparisons.
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _sentiment_label(score: float) -> str:
    """Return a short human-readable tag for the sentiment bucket."""
    if score > SENTIMENT_BOOST:
        return "boost"
    if score < SENTIMENT_DELAY:
        return "delay"
    return "neutral"


def evaluate_signal(
    indicators: dict[str, Any],
    sentiment: float,
    config: EtfSignalConfig,
) -> tuple[str, str] | None:
    """Decide whether the current readings warrant a buy notification.

    Returns ``(label, reason)`` on a triggered signal, otherwise ``None``.

    Rules (mirroring the task spec):

    * **Strong Buy** — ``rsi < rsi_oversold`` AND ``price <= bb_lower`` AND
      ``price >= sma_200``.
    * **Moderate Buy** — ``rsi < rsi_moderate`` AND (``price <= bb_lower``
      OR ``price`` is within ±2 % of ``sma_200``).
    * **Sentiment delay** (``sentiment < SENTIMENT_DELAY``) — suppress the
      notification entirely; we still log a one-liner. Boost (>0.2) does
      *not* upgrade the label, but the ``reason`` string records the
      bucket so a human can see the multiplier at a glance.
    """
    rsi = _safe_float(indicators.get("rsi"))
    price = _safe_float(indicators.get("price"))
    bb_lower = _safe_float(indicators.get("bb_lower"))
    sma_200 = _safe_float(indicators.get("sma_200"))
    if any(v is None for v in (rsi, price, bb_lower, sma_200)) or sma_200 == 0:
        return None

    sentiment_bucket = _sentiment_label(sentiment)
    if sentiment_bucket == "delay":
        # Negative-sentiment override: defer the signal until the news
        # picture improves. We deliberately drop it here so noisy
        # headlines don't trigger trades during bearish tape.
        logger.info(
            "Sentiment %.2f below delay threshold; suppressing signal",
            sentiment,
        )
        return None

    rsi_oversold = float(config.thresholds.rsi_oversold)
    rsi_moderate = float(config.thresholds.rsi_moderate)

    # Strong Buy ---------------------------------------------------------
    if rsi < rsi_oversold and price <= bb_lower and price >= sma_200:
        reason = (
            f"RSI {rsi:.2f} < {rsi_oversold:g}; "
            f"price ${price:.2f} <= BB lower ${bb_lower:.2f}; "
            f"price >= SMA200 ${sma_200:.2f}"
        )
        return ("STRONG_BUY", f"{reason} | sentiment={sentiment_bucket}")

    # Moderate Buy -------------------------------------------------------
    near_sma = abs(price - sma_200) / sma_200 <= _SMA_NEAR_TOLERANCE
    triggers_bb = price <= bb_lower
    if rsi < rsi_moderate and (triggers_bb or near_sma):
        if triggers_bb:
            trigger_clause = (
                f"price ${price:.2f} <= BB lower ${bb_lower:.2f}"
            )
        else:
            trigger_clause = (
                f"price ${price:.2f} within ±{_SMA_NEAR_TOLERANCE:.0%} of "
                f"SMA200 ${sma_200:.2f}"
            )
        reason = (
            f"RSI {rsi:.2f} < {rsi_moderate:g}; {trigger_clause}"
        )
        return ("MODERATE_BUY", f"{reason} | sentiment={sentiment_bucket}")

    return None


def _format_field(value: float | None, *, prefix: str = "$", decimals: int = 2) -> str:
    """Format a numeric indicator for an EmbedField value cell."""
    if value is None:
        return "n/a"
    return f"{prefix}{value:.{decimals}f}"


def build_embed(
    symbol: str,
    indicators: dict[str, Any],
    sentiment: float,
    label: str,
    reason: str,
    config: EtfSignalConfig,
) -> Embed:
    """Build the Discord embed for a triggered signal.

    Uses ``config.discord.default_color`` (green ``0x57F287`` by default)
    and an emoji-prefixed title.
    """
    fields: list[EmbedField] = [
        EmbedField(name="Signal", value=label, inline=True),
        EmbedField(
            name="Price",
            value=_format_field(_safe_float(indicators.get("price"))),
            inline=True,
        ),
        EmbedField(
            name="RSI",
            value=_format_field(
                _safe_float(indicators.get("rsi")), prefix="", decimals=2
            ),
            inline=True,
        ),
        EmbedField(
            name="SMA(20)",
            value=_format_field(_safe_float(indicators.get("sma_20"))),
            inline=True,
        ),
        EmbedField(
            name="SMA(50)",
            value=_format_field(_safe_float(indicators.get("sma_50"))),
            inline=True,
        ),
        EmbedField(
            name="SMA(200)",
            value=_format_field(_safe_float(indicators.get("sma_200"))),
            inline=True,
        ),
        EmbedField(
            name="BB Lower",
            value=_format_field(_safe_float(indicators.get("bb_lower"))),
            inline=True,
        ),
        EmbedField(
            name="BB Upper",
            value=_format_field(_safe_float(indicators.get("bb_upper"))),
            inline=True,
        ),
        EmbedField(
            name="Sentiment",
            value=f"{sentiment:+.2f}",
            inline=True,
        ),
    ]

    return Embed(
        title=f"{_BUY_TITLE_PREFIX}{symbol}",
        description=reason,
        color=int(config.discord.default_color),
        fields=fields,
        timestamp=datetime.now(UTC).isoformat(),
        footer="etf-signal",
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


_SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    label TEXT NOT NULL,
    reason TEXT NOT NULL,
    price REAL NOT NULL,
    rsi REAL NOT NULL,
    sma_20 REAL NOT NULL,
    sma_50 REAL NOT NULL,
    sma_200 REAL NOT NULL,
    bb_lower REAL NOT NULL,
    bb_upper REAL NOT NULL,
    sentiment REAL NOT NULL
)
"""

_INSERT_SIGNAL = """
INSERT INTO signals (
    ts, symbol, label, reason, price, rsi, sma_20, sma_50, sma_200,
    bb_lower, bb_upper, sentiment
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _ensure_schema(db: Database) -> None:
    """Idempotently create the ``signals`` table."""
    db.execute_write(_SIGNALS_SCHEMA)


def _store_signal(
    db: Database,
    symbol: str,
    label: str,
    reason: str,
    indicators: dict[str, Any],
    sentiment: float,
) -> None:
    """Persist one triggered signal. Logs and re-raises on failure."""
    try:
        db.execute_write(
            _INSERT_SIGNAL,
            (
                datetime.now(UTC).isoformat(),
                symbol,
                label,
                reason,
                _safe_float(indicators.get("price")) or 0.0,
                _safe_float(indicators.get("rsi")) or 0.0,
                _safe_float(indicators.get("sma_20")) or 0.0,
                _safe_float(indicators.get("sma_50")) or 0.0,
                _safe_float(indicators.get("sma_200")) or 0.0,
                _safe_float(indicators.get("bb_lower")) or 0.0,
                _safe_float(indicators.get("bb_upper")) or 0.0,
                float(sentiment),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — DB write must be logged loudly
        logger.error("Failed to persist signal for %s: %s", symbol, exc)
        raise


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


async def run(
    config: EtfSignalConfig,
    db_path: Path | None = None,
    notifier: Notifier | None = None,
) -> int:
    """Run one pipeline iteration. Returns the number of signals dispatched.

    ``db_path`` defaults to :data:`DEFAULT_DB_PATH`. Pass ``notifier`` to
    inject a mock in tests; otherwise :func:`create_notifier` builds one
    from ``config.discord`` (which may itself raise :class:`NotifyError`
    when no webhook is configured — caught and logged so the loop still
    completes for the remaining symbols).
    """
    resolved_db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    signals_dispatched = 0

    db = Database(resolved_db_path)
    try:
        _ensure_schema(db)

        if notifier is None:
            try:
                notifier = create_notifier(config.discord)
            except NotifyError as exc:
                logger.error(
                    "Notifier setup failed (%s); continuing without notify",
                    exc,
                )
                notifier = None

        for symbol in config.symbols:
            logger.info("Processing %s", symbol)
            indicators = await compute_indicators(symbol, config)
            if indicators is None:
                logger.warning(
                    "Skipping %s: indicator computation failed",
                    symbol,
                )
                continue

            sentiment = await analyze_sentiment(symbol, config)
            signal = evaluate_signal(indicators, sentiment, config)
            if signal is None:
                logger.info("No signal for %s", symbol)
                continue

            label, reason = signal

            # Persist first; notify second. A failed DB write should not
            # block Discord delivery (and vice-versa).
            try:
                _store_signal(db, symbol, label, reason, indicators, sentiment)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "DB persistence failed for %s: %s", symbol, exc
                )

            if notifier is not None:
                embed = build_embed(
                    symbol, indicators, sentiment, label, reason, config
                )
                ok = await notifier.send_embed(embed)
                if ok:
                    signals_dispatched += 1
                else:
                    logger.warning("Discord delivery failed for %s", symbol)
            else:
                # No notifier available — count the signal anyway so
                # callers can verify pipeline correctness.
                signals_dispatched += 1

            logger.info("Signal %s dispatched for %s", label, symbol)
    finally:
        db.close()
        if notifier is not None:
            await notifier.close()

    return signals_dispatched


__all__ = [
    "DEFAULT_DB_PATH",
    "build_embed",
    "evaluate_signal",
    "run",
]
