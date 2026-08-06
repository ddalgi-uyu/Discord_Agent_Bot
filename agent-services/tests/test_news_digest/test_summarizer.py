"""Unit tests for :mod:`news_digest.summarizer`.

The Anthropic / OpenAI SDK clients are mocked at the class level so no
network calls are made and no API keys are required.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from news_digest.config import AIConfig
from news_digest.rss_fetcher import Article
from news_digest.summarizer import build_prompt, summarize

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_articles() -> list[Article]:
    return [
        Article(
            title="OpenAI releases new SDK",
            link="https://example.com/openai",
            summary="A new SDK is available for developers.",
            published=datetime(2026, 8, 6, 10, 0, tzinfo=UTC),
            feed_name="Tech Feed",
            category="tech",
        ),
        Article(
            title="Markets rally on rate decision",
            link="https://example.com/markets",
            summary="Equities posted broad gains after the Fed's announcement.",
            published=datetime(2026, 8, 6, 9, 0, tzinfo=UTC),
            feed_name="Finance Feed",
            category="finance",
        ),
    ]


def _anthropic_response(text: str) -> Any:
    """Build a fake Anthropic ``Message`` response with the given text."""
    block = MagicMock()
    block.text = text
    message = MagicMock()
    message.content = [block]
    return message


def _openai_response(text: str) -> Any:
    """Build a fake OpenAI ``ChatCompletion`` response with the given text."""
    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    return response


ANTHROPIC_CFG = AIConfig(
    provider="anthropic",
    model="claude-3-5-sonnet-latest",
    max_items=10,
    max_bullets_per_category=3,
    max_output_chars=2000,
)
OPENAI_CFG = AIConfig(
    provider="openai",
    model="gpt-4o-mini",
    max_items=10,
    max_bullets_per_category=3,
    max_output_chars=2000,
)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    """``build_prompt`` emits the constraints and groups by category."""

    def test_system_prompt_contains_constraints(self) -> None:
        system, _ = build_prompt(
            _make_articles(),
            max_bullets_per_category=5,
            max_output_chars=1500,
        )
        assert "at most 5 bullet points" in system
        assert "under 1500 characters" in system
        assert "Format in markdown" in system

    def test_user_prompt_groups_by_category(self) -> None:
        _, user = build_prompt(
            _make_articles(),
            max_bullets_per_category=3,
            max_output_chars=2000,
        )
        # Each category is rendered as a level-2 heading followed by its items.
        assert "## tech" in user
        assert "## finance" in user
        assert "OpenAI releases new SDK" in user
        assert "Markets rally on rate decision" in user

    def test_user_prompt_contains_article_metadata(self) -> None:
        _, user = build_prompt(
            _make_articles(),
            max_bullets_per_category=3,
            max_output_chars=2000,
        )
        assert "https://example.com/openai" in user
        assert "Tech Feed" in user
        assert "Summary:" in user

    def test_empty_articles_yields_placeholder_user_prompt(self) -> None:
        _, user = build_prompt(
            [],
            max_bullets_per_category=3,
            max_output_chars=2000,
        )
        assert "No articles" in user


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    """``provider='anthropic'`` calls ``anthropic.AsyncAnthropic`` once."""

    @pytest.mark.asyncio
    async def test_returns_model_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
        expected_text = "## tech\n- OpenAI released a new SDK today.\n## finance\n- Markets rallied."

        with patch("anthropic.AsyncAnthropic") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_anthropic_response(expected_text)
            )
            mock_client.return_value = instance

            result = await summarize(_make_articles(), ANTHROPIC_CFG)

        assert result == expected_text
        mock_client.assert_called_once_with(api_key="test-anthropic-key")

    @pytest.mark.asyncio
    async def test_batch_mode_makes_one_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All articles are sent in a single ``messages.create`` call."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        with patch("anthropic.AsyncAnthropic") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_anthropic_response("ok")
            )
            mock_client.return_value = instance

            await summarize(_make_articles() * 5, ANTHROPIC_CFG)

        assert instance.messages.create.await_count == 1

    @pytest.mark.asyncio
    async def test_prompt_contains_batches_and_constraints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        with patch("anthropic.AsyncAnthropic") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_anthropic_response("ok")
            )
            mock_client.return_value = instance

            await summarize(_make_articles(), ANTHROPIC_CFG)

        # Inspect the call we made.
        call_kwargs = instance.messages.create.await_args.kwargs
        assert call_kwargs["model"] == "claude-3-5-sonnet-latest"
        assert "max_tokens" in call_kwargs
        assert "messages" in call_kwargs
        # System prompt carries the requested constraints.
        system = call_kwargs["system"]
        assert "at most 3 bullet points" in system
        assert "under 2000 characters" in system

    @pytest.mark.asyncio
    async def test_respects_max_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        limited_cfg = ANTHROPIC_CFG.model_copy(update={"max_items": 1})

        with patch("anthropic.AsyncAnthropic") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_anthropic_response("ok")
            )
            mock_client.return_value = instance

            await summarize(_make_articles(), limited_cfg)

        # The user message body should reference exactly one article title.
        call_kwargs = instance.messages.create.await_args.kwargs
        user_text = call_kwargs["messages"][0]["content"]
        assert "OpenAI releases new SDK" in user_text
        assert "Markets rally on rate decision" not in user_text

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error_string(self) -> None:
        # Explicitly clear the env var so it cannot leak in.
        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("ANTHROPIC_API_KEY", None)

            result = await summarize(_make_articles(), ANTHROPIC_CFG)

        assert "News digest generation failed" in result
        assert "anthropic error" in result

    @pytest.mark.asyncio
    async def test_api_error_returns_error_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        with patch("anthropic.AsyncAnthropic") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.messages.create = AsyncMock(
                side_effect=RuntimeError("provider 500")
            )
            mock_client.return_value = instance

            result = await summarize(_make_articles(), ANTHROPIC_CFG)

        assert "News digest generation failed" in result
        assert "RuntimeError" in result
        assert "provider 500" in result


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    """``provider='openai'`` calls ``openai.AsyncOpenAI`` once."""

    @pytest.mark.asyncio
    async def test_returns_model_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        expected_text = "## tech\n- summary bullet"

        with patch("openai.AsyncOpenAI") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.chat.completions.create = AsyncMock(
                return_value=_openai_response(expected_text)
            )
            mock_client.return_value = instance

            result = await summarize(_make_articles(), OPENAI_CFG)

        assert result == expected_text
        mock_client.assert_called_once_with(api_key="test-openai-key")

    @pytest.mark.asyncio
    async def test_batch_mode_makes_one_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "k")

        with patch("openai.AsyncOpenAI") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.chat.completions.create = AsyncMock(
                return_value=_openai_response("ok")
            )
            mock_client.return_value = instance

            await summarize(_make_articles() * 4, OPENAI_CFG)

        assert instance.chat.completions.create.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error_string(self) -> None:
        import os as _os

        with patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("OPENAI_API_KEY", None)

            result = await summarize(_make_articles(), OPENAI_CFG)

        assert "News digest generation failed" in result
        assert "openai error" in result

    @pytest.mark.asyncio
    async def test_api_error_returns_error_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "k")

        with patch("openai.AsyncOpenAI") as mock_client:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.chat.completions.create = AsyncMock(
                side_effect=ConnectionError("network down")
            )
            mock_client.return_value = instance

            result = await summarize(_make_articles(), OPENAI_CFG)

        assert "News digest generation failed" in result
        assert "ConnectionError" in result


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class TestProviderEdgeCases:
    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # ``AIConfig`` is pydantic-validated to a literal, so use a duck-typed
        # object that exposes the same attributes with an unsupported value.
        weird_cfg = MagicMock()
        weird_cfg.provider = "google"
        weird_cfg.model = "x"
        weird_cfg.max_items = 10
        weird_cfg.max_bullets_per_category = 3
        weird_cfg.max_output_chars = 2000

        result = await summarize(_make_articles(), weird_cfg)

        assert "Unknown AI provider" in result
