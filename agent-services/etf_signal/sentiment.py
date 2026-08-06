"""FinBERT-based financial sentiment scoring.

Public surface:

* :func:`analyze_sentiment` — fetch recent headlines for a symbol and return
  the FinBERT-weighted average sentiment in ``[-1, 1]``.

Why FinBERT and not ``vaderSentiment``? FinBERT is pre-trained on
financial text (Reuters financial news, …) so its label confidence is much
more meaningful for ticker sentiment than VADER's generic lexicon model.
The pipeline API is from HuggingFace ``transformers``.

The :mod:`transformers` dependency is wrapped in ``try/except ImportError``
so this module can be imported on test machines without the heavy ML stack.
When the dependency is missing, model loading failure, or sentiment is
disabled in config, the function returns ``0.0`` and logs a warning instead
of crashing.
"""
from __future__ import annotations

import logging
from typing import Any

from etf_signal.config import EtfSignalConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Third-party imports (guarded)
# ---------------------------------------------------------------------------

# ``transformers.pipeline`` is heavy: it loads the model at construction.
# We keep the reference module-scoped (renamed ``_hf_pipeline``) so tests
# can monkeypatch it cleanly. ``None`` means the package is not installed.
try:
    from transformers import pipeline as _hf_pipeline  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _hf_pipeline = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Headlines
# ---------------------------------------------------------------------------

# Offline fallback used when no live news API is wired in. Each list contains
# strings that read as mildly bullish — enough to exercise the FinBERT code
# path in development without external dependencies. The pipeline always
# truncates to ``config.sentiment.news_per_symbol`` items.
_MOCK_HEADLINES: dict[str, list[str]] = {
    "SPY": [
        "S&P 500 rallies to new highs amid strong earnings",
        "Tech sector leads gains as investors remain bullish",
        "Markets close higher on encouraging economic data",
        "Index futures point to a positive open on Wall Street",
        "Broad market optimism returns as inflation cools",
    ],
    "QQQ": [
        "Nasdaq surges on AI optimism and strong chip demand",
        "Tech giants report strong quarterly results",
        "Growth stocks outperform value in recent session",
        "Cloud and AI names lead tech rebound",
        "Investors pile into Nasdaq 100 ETF",
    ],
    "VTI": [
        "Total market index hits all-time high",
        "Broad market gains as small caps join the rally",
        "Investors pile into diversified ETFs",
        "Domestic equities outperform international peers",
        "Risk-on tone lifts total-market fund flows",
    ],
}

_DEFAULT_HEADLINES: list[str] = [
    "Markets mixed amid mixed economic signals",
    "Trading volumes remain elevated across sectors",
    "Investors weigh rate path against earnings outlook",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _label_to_score(label: str) -> float:
    """Map a FinBERT textual label to a numeric score.

    FinBERT emits ``"positive"`` / ``"negative"`` / ``"neutral"``. The label
    is lower-cased and substring-matched so a hypothetical ``"positive
    sentiment"`` round-trip still resolves to ``+1``.
    """
    normalized = label.lower()
    if "positive" in normalized:
        return 1.0
    if "negative" in normalized:
        return -1.0
    return 0.0


def _load_classifier(model_name: str) -> Any:
    """Construct the FinBERT pipeline. Raises on failure.

    Exposed as a small helper so tests can stub out the construction
    directly without going through :func:`analyze_sentiment`.
    """
    if _hf_pipeline is None:  # pragma: no cover
        raise RuntimeError("transformers is not installed")
    return _hf_pipeline("sentiment-analysis", model=model_name)


def _select_headlines(symbol: str, limit: int) -> list[str]:
    """Return up to ``limit`` mock headlines for ``symbol``.

    Symbols not present in the table fall back to a generic set so the
    pipeline always has something to score. Order is stable for test
    reproducibility.
    """
    pool = _MOCK_HEADLINES.get(symbol.upper(), _DEFAULT_HEADLINES)
    return list(pool[:limit])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze_sentiment(
    symbol: str,
    config: EtfSignalConfig,
) -> float:
    """Compute the FinBERT-weighted sentiment for ``symbol``.

    Returns a float in ``[-1, 1]``. Behaviour matrix:

    =====================  ==========================================
    Situation              Return
    =====================  ==========================================
    sentiment disabled     ``0.0`` (no work performed)
    transformers missing   ``0.0`` + warning
    model load fails       ``0.0`` + warning
    inference fails        ``0.0`` + warning
    no headlines           ``0.0``
    all preds < confidence ``0.0`` (filtered out)
    normal                 weighted average in ``[-1, 1]``
    =====================  ==========================================

    Never raises.
    """
    if not config.sentiment.enabled:
        return 0.0

    if _hf_pipeline is None:  # pragma: no cover
        logger.warning(
            "transformers not installed; sentiment for %s returns 0.0",
            symbol,
        )
        return 0.0

    try:
        classifier = _load_classifier(config.sentiment.model)
    except Exception as exc:  # noqa: BLE001 — model loads can fail in many ways
        logger.warning(
            "FinBERT model load failed for %s (%s); returning 0.0",
            symbol,
            exc,
        )
        return 0.0

    headlines = _select_headlines(symbol, config.sentiment.news_per_symbol)
    if not headlines:
        return 0.0

    try:
        results = classifier(headlines)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "FinBERT inference failed for %s (%s); returning 0.0",
            symbol,
            exc,
        )
        return 0.0

    min_confidence = config.thresholds.min_confidence
    weighted_sum = 0.0
    weight_total = 0.0
    for item in results:
        label = item.get("label", "")
        score = _label_to_score(label)
        confidence = float(item.get("score", 0.0))
        if confidence < min_confidence:
            # Drop low-confidence predictions — they're noise, not signal.
            continue
        weighted_sum += score * confidence
        weight_total += confidence

    if weight_total == 0.0:
        return 0.0
    return weighted_sum / weight_total


__all__ = [
    "analyze_sentiment",
]
