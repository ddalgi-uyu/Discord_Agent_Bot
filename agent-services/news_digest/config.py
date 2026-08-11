"""News digest application configuration.

Defines the pydantic model that extends :class:`common.config_loader.AppConfig`
with news-digest-specific fields (``feeds`` and ``ai``). Loaded by the standard
``common.config_loader.load_config`` machinery — see
``config/news_digest.yaml`` for the corresponding YAML file.

Environment variable naming follows the standard ``NEWS_DIGEST_*`` convention
documented in ``common.config_loader``. Examples::

    NEWS_DIGEST_AI__PROVIDER=openai
    NEWS_DIGEST_AI__MODEL=gpt-4o-mini
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from common.config_loader import AppConfig

__all__ = [
    "FeedConfig",
    "AIConfig",
    "NewsDigestConfig",
]


class FeedConfig(BaseModel):
    """A single RSS / Atom feed subscription."""

    name: str
    url: str
    category: str


class AIConfig(BaseModel):
    """AI summarisation settings.
    
    Defaults match ``config/news_digest.yaml``. ``max_output_chars`` is a soft
    ceiling enforced via the system prompt; the underlying API call is not
    constrained beyond ``max_tokens`` for Anthropic.
    """
    
    provider: Literal["anthropic", "openai", "fallback"] = "anthropic"
    model: str = "claude-3-5-sonnet-latest"
    # Hard cap on the number of articles sent to the model in one batch.
    max_items: int = Field(default=10, ge=1)
    # Number of bullet points requested per category in the prompt.
    max_bullets_per_category: int = Field(default=3, ge=1)
    # Soft ceiling on the rendered markdown output, advertised to the model.
    max_output_chars: int = Field(default=2000, ge=100)



class NewsDigestConfig(AppConfig):
    """Top-level news-digest configuration.

    Inherits ``app_name``, ``discord``, ``logging``, ``schedule`` and
    ``timezone`` from :class:`AppConfig`. Adds feed subscriptions and AI
    summarisation settings.
    """

    feeds: list[FeedConfig] = Field(default_factory=list)
    ai: AIConfig = AIConfig()
