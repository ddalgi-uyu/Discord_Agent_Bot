"""Configuration model for the ``etf-signal`` application.

Subclasses :class:`common.config_loader.AppConfig` so the standard
``load_config`` helper validates this model against the merged YAML +
environment-variable input. All thresholds default to the values shipped in
``config/etf_signal.yaml``; override any subset via YAML keys or
``ETFSIGNAL_*`` environment variables.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from common.config_loader import AppConfig

__all__ = [
    "IndicatorConfig",
    "SentimentConfig",
    "ThresholdConfig",
    "EtfSignalConfig",
]


class IndicatorConfig(BaseModel):
    """Technical-indicator tuning parameters.

    Defaults match the canonical ``RSI(14) / SMA(20,50,200) / BB(20,2)``
    configuration. ``bollinger_std`` is a float so non-integer multipliers
    (e.g. ``1.5``) can be expressed in YAML without quoting.
    """

    rsi_period: int = 14
    sma_periods: list[int] = Field(default_factory=lambda: [20, 50, 200])
    bollinger_period: int = 20
    bollinger_std: float = 2.0


class SentimentConfig(BaseModel):
    """FinBERT-based sentiment scoring parameters.

    ``model`` is the HuggingFace model id passed to ``transformers.pipeline``.
    ``news_per_symbol`` caps the number of headlines fed into the model per
    symbol so we don't burn CPU on marginal additions.
    """

    enabled: bool = True
    model: str = "ProsusAI/finbert"
    news_per_symbol: int = 5


class ThresholdConfig(BaseModel):
    """Buy-signal decision thresholds.

    ``rsi_oversold`` is the cut-off for the Strong-Buy rule; ``rsi_moderate``
    is the (looser) cut-off for the Moderate-Buy rule.
    ``min_confidence`` filters FinBERT predictions — anything below this
    FinBERT score is dropped from the sentiment average.
    """

    rsi_oversold: float = 30.0
    rsi_moderate: float = 35.0
    min_confidence: float = 0.6


class EtfSignalConfig(AppConfig):
    """Top-level ``etf-signal`` configuration.

    Adds the four ETF-specific sections on top of :class:`AppConfig`:
    ``symbols``, ``indicators``, ``sentiment`` and ``thresholds``.
    """

    symbols: list[str] = Field(default_factory=list)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
