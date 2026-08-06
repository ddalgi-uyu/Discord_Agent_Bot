"""Configuration model for the ``currency-notifier`` app.

Subclasses :class:`common.config_loader.AppConfig` to add the list of currency
pairs that the notifier tracks. Each pair has an optional static threshold and
an optional dynamic (z-score based) threshold configuration.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from common.config_loader import AppConfig

__all__ = [
    "DynamicThresholdConfig",
    "PairConfig",
    "CurrencyNotifierConfig",
]


class DynamicThresholdConfig(BaseModel):
    """Configuration for a z-score based dynamic threshold.

    ``z_threshold`` is typically negative so the alert fires when the rate
    drops meaningfully below the recent mean (e.g. ``-1.5`` = 1.5 standard
    deviations below the windowed mean).
    """

    window_days: int = Field(default=7, ge=1)
    z_threshold: float = -1.5


class PairConfig(BaseModel):
    """A single currency pair plus its alert configuration.

    All threshold fields are optional; an empty pair (no static, no dynamic,
    no percentage change) is still valid but will never fire alerts.
    """

    base: str
    quote: str
    static_threshold: float | None = None
    dynamic: DynamicThresholdConfig | None = None
    #: Absolute percent change over the trailing 7 days required to fire
    #: a "large move" alert. ``None`` disables the check.
    change_threshold_pct: float | None = None

    @field_validator("base", "quote")
    @classmethod
    def _normalise_currency_code(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned or not cleaned.isalpha():
            raise ValueError(
                f"currency code must be non-empty alphabetic, got {value!r}"
            )
        return cleaned


class CurrencyNotifierConfig(AppConfig):
    """Top-level configuration for the ``currency-notifier`` app.

    Inherits all standard ``AppConfig`` fields (``app_name``, ``discord``,
    ``logging``, ``schedule``, ``timezone``) and adds the list of currency
    pairs the notifier tracks.
    """

    pairs: list[PairConfig] = Field(default_factory=list)
