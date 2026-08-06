"""Threshold evaluation for ``currency-notifier``.

Pure-function module — no I/O, no logging side effects — so it is trivially
testable. Three independent checks are exposed:

* **Static** — fire when ``rate <= static_threshold``.
* **Dynamic (z-score)** — fire when the current rate is more than
  ``z_threshold`` standard deviations below the mean of the trailing
  ``window_days`` observations.
* **Percentage change** — fire when the absolute change between the current
  rate and the rate from 7 observations ago exceeds ``change_threshold_pct``.

All checks are *opt-in*: missing configuration means the check is skipped,
never an error. Empty / short history silently disables the checks that
require a window of samples.
"""
from __future__ import annotations

import math
from typing import Any

__all__ = ["evaluate_thresholds"]


#: How many observations back to compare for the percentage-change check.
_CHANGE_LOOKBACK: int = 7


def _pair_label(pair_config: dict[str, Any]) -> str:
    base = pair_config.get("base", "?")
    quote = pair_config.get("quote", "?")
    return f"{base}/{quote}"


def _evaluate_static(
    pair_config: dict[str, Any], rate: float, pair: str
) -> list[dict[str, Any]]:
    static = pair_config.get("static_threshold")
    if static is None:
        return []
    try:
        static_value = float(static)
    except (TypeError, ValueError):
        return []
    if rate <= static_value:
        return [
            {
                "type": "static",
                "pair": pair,
                "rate": rate,
                "threshold": static_value,
                "message": (
                    f"Rate is at or below target ({rate:.4f} <= {static_value:.4f})"
                ),
            }
        ]
    return []


def _evaluate_dynamic(
    pair_config: dict[str, Any],
    rate: float,
    history: list[float],
    pair: str,
) -> list[dict[str, Any]]:
    dynamic = pair_config.get("dynamic")
    if not dynamic:
        return []

    try:
        window_days = max(1, int(dynamic.get("window_days", 7)))
        z_threshold = float(dynamic.get("z_threshold", -1.5))
    except (TypeError, ValueError):
        return []

    # Take the most recent ``window_days`` samples. If the history is shorter
    # than that, use what's available; if it's still too short (< 2 samples)
    # we cannot compute a meaningful z-score and silently skip.
    if len(history) < 2:
        return []
    window = list(history[-window_days:]) if len(history) >= window_days else list(history)
    if len(window) < 2:
        return []

    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    std = math.sqrt(variance)

    z_score = 0.0 if std == 0.0 else (rate - mean) / std

    if z_score <= z_threshold:
        return [
            {
                "type": "dynamic",
                "pair": pair,
                "rate": rate,
                "z_score": z_score,
                "mean": mean,
                "threshold": z_threshold,
                "message": (
                    f"Rate is statistically low "
                    f"(z-score: {z_score:.2f}, mean: {mean:.4f})"
                ),
            }
        ]
    return []


def _evaluate_change_pct(
    pair_config: dict[str, Any],
    rate: float,
    history: list[float],
    pair: str,
) -> list[dict[str, Any]]:
    threshold = pair_config.get("change_threshold_pct")
    if threshold is None:
        return []
    try:
        threshold_value = float(threshold)
    except (TypeError, ValueError):
        return []

    # Need at least ``_CHANGE_LOOKBACK + 1`` samples so we can compare the
    # current rate against the one ``_CHANGE_LOOKBACK`` observations ago.
    if len(history) < _CHANGE_LOOKBACK + 1:
        return []

    past_rate = history[-(_CHANGE_LOOKBACK + 1)]
    if past_rate <= 0:
        return []

    change_pct = abs((rate - past_rate) / past_rate * 100.0)
    if change_pct >= threshold_value:
        return [
            {
                "type": "change_pct",
                "pair": pair,
                "rate": rate,
                "change_pct": change_pct,
                "threshold": threshold_value,
                "message": (
                    f"{_CHANGE_LOOKBACK}-day change {change_pct:.2f}% "
                    f"exceeds threshold {threshold_value:.2f}%"
                ),
            }
        ]
    return []


def evaluate_thresholds(
    pair_config: dict[str, Any],
    rate: float,
    history: list[float],
) -> list[dict[str, Any]]:
    """Run every configured threshold check for ``rate``.

    Parameters
    ----------
    pair_config:
        Plain ``dict`` representation of a :class:`PairConfig`. Must contain
        at least ``base`` and ``quote`` for the alert ``pair`` label.
    rate:
        Current exchange rate.
    history:
        Recent rates for the pair, oldest-first (so ``history[-1]`` is the
        most recent past observation). May be empty or shorter than the
        configured window — checks that need samples will simply be skipped.

    Returns
    -------
    A list of triggered alerts. Each alert is a ``dict`` with at least the
    keys ``type``, ``pair``, ``rate``, and ``message`` (additional keys are
    added per ``type``: ``threshold``, ``z_score``, ``mean``, ``change_pct``).
    An empty list means no thresholds fired.
    """
    pair = _pair_label(pair_config)
    alerts: list[dict[str, Any]] = []

    # Static check needs no history at all — run it first so a pair with no
    # history still benefits from absolute thresholds.
    alerts.extend(_evaluate_static(pair_config, rate, pair))
    alerts.extend(_evaluate_dynamic(pair_config, rate, history, pair))
    alerts.extend(_evaluate_change_pct(pair_config, rate, history, pair))

    return alerts
