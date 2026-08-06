"""Technical indicator computation for a single ETF symbol.

Public surface:

* :func:`compute_indicators` — fetch one year of daily OHLCV via ``yfinance``
  and compute ``RSI``, ``SMA(20/50/200)`` and ``Bollinger Bands(20, 2)``.

The third-party imports (``yfinance``, ``pandas_ta``) are wrapped in
``try/except ImportError`` so this module can be imported on test machines
that don't have them installed. The functions guard against ``None`` and log
a warning instead of crashing.

The downstream DataFrame validation is deliberately defensive — yfinance is
notoriously flaky and may return empty frames, missing columns, NaN, or
``±inf`` in price columns. We never raise; we log and return ``None``.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from etf_signal.config import EtfSignalConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Third-party imports (guarded)
# ---------------------------------------------------------------------------

# Exposed at module scope so tests can monkeypatch these references without
# touching the global import system. ``None`` means the library is not
# installed in the current interpreter; the runtime guards below translate
# that into a logged warning + ``None`` return value rather than a crash.
try:
    import yfinance as yf  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment,misc]

try:
    import pandas_ta as ta  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    ta = None  # type: ignore[assignment,misc]


# Sentinels used by the validation helper. These are kept module-private
# because they describe what the helper considers "non-finite" — not part of
# the public contract.
_NAN_SENTINEL: float = float("nan")
_INF_SENTINEL: float = float("inf")


# ---------------------------------------------------------------------------
# DataFrame validation
# ---------------------------------------------------------------------------


def _is_non_finite(value: Any) -> bool:
    """Return True for ``NaN`` / ``+inf`` / ``-inf`` / non-numeric values.

    Duck-typed — uses :func:`math.isfinite` so we don't need to depend on
    ``numpy`` to perform the check.
    """
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        # ``True``/``False`` are finite but not really numeric; treat any
        # non-convertible value as non-finite so it fails the validation.
        return True


def validate_dataframe(df: Any, symbol: str) -> bool:
    """Ensure ``df`` is usable for indicator computation.

    Rejects ``df`` if any of the following hold:

    * ``df`` is ``None`` or its ``empty`` attribute is truthy.
    * The ``Close`` column is missing.
    * The ``Close`` series contains any ``NaN``.
    * The ``Close`` series contains any ``±inf`` or otherwise non-finite
      value.

    On failure a warning is logged explaining which check tripped. Returns
    ``True`` only when every check passes.
    """
    if df is None:
        logger.warning("yfinance returned None for %s", symbol)
        return False

    empty_attr = getattr(df, "empty", None)
    if empty_attr is True:
        logger.warning("yfinance returned empty DataFrame for %s", symbol)
        return False

    columns = getattr(df, "columns", None)
    if columns is None or "Close" not in columns:
        logger.warning(
            "yfinance DataFrame for %s missing 'Close' column (got %r)",
            symbol,
            list(columns) if columns is not None else None,
        )
        return False

    close = df["Close"]
    isna_attr = getattr(close, "isna", None)
    if isna_attr is not None and bool(isna_attr().any()):
        logger.warning("yfinance DataFrame for %s has NaN in 'Close'", symbol)
        return False

    # Inf / NaN walk — duck-typed to avoid a numpy dependency.
    for raw in close:
        if _is_non_finite(raw):
            logger.warning(
                "yfinance DataFrame for %s has non-finite value in 'Close'",
                symbol,
            )
            return False

    return True


# Public alias so tests can exercise the validator without going through
# the full pipeline. Kept identical to :func:`validate_dataframe`.
is_valid_dataframe = validate_dataframe


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_indicators(
    symbol: str,
    config: EtfSignalConfig,
) -> dict[str, Any] | None:
    """Compute the latest RSI / SMA / Bollinger values for ``symbol``.

    Returns a dict ``{symbol, price, rsi, sma_20, sma_50, sma_200, bb_lower,
    bb_mid, bb_upper}`` on success, or ``None`` when:

    * ``yfinance`` / ``pandas_ta`` are not installed,
    * the download fails (network errors, rate limits, …),
    * the returned DataFrame fails validation.

    Never raises. Errors are logged via :mod:`logging`.
    """
    if yf is None:  # pragma: no cover
        logger.warning(
            "yfinance not installed; cannot compute indicators for %s",
            symbol,
        )
        return None
    if ta is None:  # pragma: no cover
        logger.warning(
            "pandas_ta not installed; cannot compute indicators for %s",
            symbol,
        )
        return None

    try:
        df = yf.download(  # type: ignore[union-attr]
            symbol,
            period="1y",
            interval="1d",
            progress=False,
        )
    except Exception as exc:  # noqa: BLE001 — yfinance raises everything
        logger.warning("yfinance download failed for %s: %s", symbol, exc)
        return None

    if not validate_dataframe(df, symbol):
        return None

    close = df["Close"].astype(float)

    # ---- RSI --------------------------------------------------------------
    rsi_period = config.indicators.rsi_period
    rsi_series = ta.rsi(close, length=rsi_period)  # type: ignore[union-attr]
    rsi_val = float(rsi_series.iloc[-1])

    # ---- SMAs -------------------------------------------------------------
    sma_values: dict[int, float] = {}
    for period in config.indicators.sma_periods:
        sma_series = ta.sma(close, length=period)  # type: ignore[union-attr]
        sma_values[period] = float(sma_series.iloc[-1])

    # ---- Bollinger Bands --------------------------------------------------
    bb_period = config.indicators.bollinger_period
    bb_std = config.indicators.bollinger_std
    bbands_df = ta.bbands(  # type: ignore[union-attr]
        close,
        length=bb_period,
        std=bb_std,
    )
    # pandas_ta names the columns ``BBL_{p}_{std}``, ``BBM_{p}_{std}``,
    # ``BBU_{p}_{std}``. We resolve by prefix so the code survives any
    # pandas_ta rename across versions.
    bb_lower_col = next(
        (c for c in bbands_df.columns if c.startswith(f"BBL_{bb_period}_")),
        None,
    )
    bb_mid_col = next(
        (c for c in bbands_df.columns if c.startswith(f"BBM_{bb_period}_")),
        None,
    )
    bb_upper_col = next(
        (c for c in bbands_df.columns if c.startswith(f"BBU_{bb_period}_")),
        None,
    )
    if bb_lower_col is None or bb_mid_col is None or bb_upper_col is None:
        logger.warning(
            "pandas_ta.bbands returned unexpected columns %r for %s",
            list(bbands_df.columns),
            symbol,
        )
        return None

    bb_lower = float(bbands_df[bb_lower_col].iloc[-1])
    bb_mid = float(bbands_df[bb_mid_col].iloc[-1])
    bb_upper = float(bbands_df[bb_upper_col].iloc[-1])

    return {
        "symbol": symbol,
        "price": float(close.iloc[-1]),
        "rsi": rsi_val,
        "sma_20": sma_values.get(20),
        "sma_50": sma_values.get(50),
        "sma_200": sma_values.get(200),
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
    }


__all__ = [
    "compute_indicators",
    "validate_dataframe",
]
