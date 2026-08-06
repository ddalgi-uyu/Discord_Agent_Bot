"""Tests for :mod:`etf_signal.sentiment`.

The :mod:`transformers` package isn't installed in the test environment, so
we monkeypatch the module-level ``_hf_pipeline`` reference and feed in a
small stub that mimics the HuggingFace ``pipeline`` shape.

No real FinBERT model is loaded. No network calls are made.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from etf_signal import sentiment as sentiment_module
from etf_signal.config import EtfSignalConfig, SentimentConfig, ThresholdConfig
from etf_signal.sentiment import (
    _label_to_score,
    _load_classifier,
    _select_headlines,
    analyze_sentiment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_classifier(predictions: list[dict[str, float | str]]) -> MagicMock:
    """Build a stub ``pipeline`` whose ``__call__`` returns ``predictions``."""
    stub = MagicMock(name="hf_pipeline_call")
    stub.return_value = predictions
    return stub


def _stub_pipeline_factory(predictions: list[dict[str, float | str]]) -> MagicMock:
    """Build a stub ``transformers.pipeline`` that returns ``predictions``."""
    factory = MagicMock(name="transformers.pipeline")
    factory.return_value = _stub_classifier(predictions)
    return factory


def _config(
    *,
    enabled: bool = True,
    model: str = "ProsusAI/finbert",
    news_per_symbol: int = 5,
    min_confidence: float = 0.6,
) -> EtfSignalConfig:
    """Build a minimal :class:`EtfSignalConfig` overriding only what matters."""
    return EtfSignalConfig(
        app_name="etf-signal",
        symbols=["SPY"],
        sentiment=SentimentConfig(
            enabled=enabled,
            model=model,
            news_per_symbol=news_per_symbol,
        ),
        thresholds=ThresholdConfig(
            rsi_oversold=30.0,
            rsi_moderate=35.0,
            min_confidence=min_confidence,
        ),
    )


# ---------------------------------------------------------------------------
# Label scoring
# ---------------------------------------------------------------------------


class TestLabelToScore:
    def test_positive_label(self) -> None:
        assert _label_to_score("positive") == 1.0

    def test_negative_label(self) -> None:
        assert _label_to_score("negative") == -1.0

    def test_neutral_label(self) -> None:
        assert _label_to_score("neutral") == 0.0

    def test_label_is_case_insensitive(self) -> None:
        assert _label_to_score("POSITIVE") == 1.0
        assert _label_to_score("Negative") == -1.0

    def test_unknown_label_maps_to_zero(self) -> None:
        assert _label_to_score("weird-label") == 0.0


# ---------------------------------------------------------------------------
# Headline selection
# ---------------------------------------------------------------------------


class TestSelectHeadlines:
    def test_known_symbol_returns_pool(self) -> None:
        result = _select_headlines("SPY", limit=10)
        assert len(result) >= 1
        assert all(isinstance(h, str) for h in result)

    def test_unknown_symbol_falls_back_to_default(self) -> None:
        result = _select_headlines("ZZZZ", limit=10)
        assert len(result) >= 1

    def test_limit_caps_length(self) -> None:
        result = _select_headlines("SPY", limit=2)
        assert len(result) == 2

    def test_limit_larger_than_pool(self) -> None:
        # Pool for SPY has 5 entries (see ``_MOCK_HEADLINES``).
        result = _select_headlines("SPY", limit=999)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# analyze_sentiment
# ---------------------------------------------------------------------------


class TestAnalyzeSentiment:
    """End-to-end coverage for the async analyze_sentiment coroutine."""

    @pytest.mark.asyncio
    async def test_disabled_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even with transformers missing, disabled short-circuits before
        # any model logic runs.
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", None)
        cfg = _config(enabled=False)
        result = await analyze_sentiment("SPY", cfg)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_transformers_missing_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", None)
        result = await analyze_sentiment("SPY", _config())
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_model_load_failure_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = MagicMock(side_effect=RuntimeError("model weights missing"))
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        result = await analyze_sentiment("SPY", _config())
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_inference_failure_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = _stub_pipeline_factory([])
        # Make the classifier itself blow up on call.
        factory.return_value.side_effect = RuntimeError("torch OOM")
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        result = await analyze_sentiment("SPY", _config())
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_all_positive_returns_positive_score(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = _stub_pipeline_factory([
            {"label": "positive", "score": 0.9},
            {"label": "positive", "score": 0.8},
        ])
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        score = await analyze_sentiment("SPY", _config())
        # All positive, all above 0.6 confidence → weighted avg ≈ +1.0.
        assert 0.0 < score <= 1.0
        assert score == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_all_negative_returns_negative_score(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = _stub_pipeline_factory([
            {"label": "negative", "score": 0.95},
            {"label": "negative", "score": 0.7},
        ])
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        score = await analyze_sentiment("SPY", _config())
        assert -1.0 <= score < 0.0
        assert score == pytest.approx(-1.0)

    @pytest.mark.asyncio
    async def test_mixed_returns_weighted_average(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = _stub_pipeline_factory([
            {"label": "positive", "score": 0.8},
            {"label": "negative", "score": 0.7},
        ])
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        score = await analyze_sentiment("SPY", _config())
        # Weighted avg = (0.8 * 1.0 + 0.7 * -1.0) / (0.8 + 0.7) = 0.1 / 1.5
        assert score == pytest.approx(0.1 / 1.5, abs=1e-6)
        assert -1.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_low_confidence_predictions_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # All below the 0.6 min-confidence threshold → weight_total == 0
        # → return 0.0.
        factory = _stub_pipeline_factory([
            {"label": "positive", "score": 0.55},
            {"label": "negative", "score": 0.5},
        ])
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        score = await analyze_sentiment("SPY", _config())
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_only_low_confidence_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = _stub_pipeline_factory([
            {"label": "neutral", "score": 0.5},
        ])
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        score = await analyze_sentiment("SPY", _config())
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_news_per_symbol_caps_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # We capture how many headlines the classifier is invoked with so
        # we can verify the ``news_per_symbol`` slice is honoured.
        seen_lengths: list[int] = []

        def _capture(headlines: list[str]) -> list[dict[str, float | str]]:
            seen_lengths.append(len(headlines))
            return [{"label": "positive", "score": 0.9}]

        factory = MagicMock()
        factory.return_value = MagicMock(side_effect=_capture)
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        score = await analyze_sentiment("SPY", _config(news_per_symbol=2))
        assert seen_lengths == [2]
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Helper: _load_classifier
# ---------------------------------------------------------------------------


class TestLoadClassifier:
    def test_returns_pipeline_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = MagicMock(name="classifier")
        factory = MagicMock(return_value=expected)
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", factory)

        result = _load_classifier("ProsusAI/finbert")
        assert result is expected
        factory.assert_called_once_with(
            "sentiment-analysis", model="ProsusAI/finbert"
        )

    def test_raises_when_transformers_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sentiment_module, "_hf_pipeline", None)
        with pytest.raises(RuntimeError, match="transformers is not installed"):
            _load_classifier("ProsusAI/finbert")
