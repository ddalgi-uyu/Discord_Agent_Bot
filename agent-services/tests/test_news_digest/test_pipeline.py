"""Unit tests for :mod:`news_digest.pipeline`.

The fetcher, summarizer and notifier are mocked at the pipeline module
boundary so :func:`news_digest.pipeline.run` can be exercised end-to-end
without touching the network or any AI SDK.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from common.config_loader import DiscordConfig
from common.discord_notifier import DEFAULT_EMBED_COLOR, Embed
from news_digest.config import AIConfig, FeedConfig, NewsDigestConfig
from news_digest.pipeline import (
    EMBED_COLOR,
    EMBED_TITLE,
    build_embed,
    parse_digest_sections,
    run,
)
from news_digest.rss_fetcher import Article

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _article(title: str, category: str = "tech", hours_ago: int = 1) -> Article:
    return Article(
        title=title,
        link=f"https://example.com/{title.replace(' ', '-')}",
        summary=f"Summary of {title}",
        published=datetime.now(tz=UTC).replace(microsecond=0)
        - _td(hours=hours_ago),
        feed_name="Test Feed",
        category=category,
    )


def _td(**kwargs: int):  # helper that doesn't collide with timedelta import
    from datetime import timedelta

    return timedelta(**kwargs)


def _config(**overrides: Any) -> NewsDigestConfig:
    base = NewsDigestConfig(
        app_name="news-digest",
        discord=DiscordConfig(
            mode="webhook",
            webhook_url="https://discord.com/api/webhooks/test/token",
            default_color=0x5865F2,
        ),
        feeds=[FeedConfig(name="X", url="https://x.example.com/rss", category="tech")],
        ai=AIConfig(),
    )
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


class _FakeNotifier:
    """Records ``send_embed`` calls and returns a configurable success flag."""

    def __init__(self, *, success: bool = True) -> None:
        self.success = success
        self.embeds: list[Embed] = []
        self.close_count = 0

    async def send_embed(self, embed: Embed) -> bool:
        self.embeds.append(embed)
        return self.success

    async def close(self) -> None:
        self.close_count += 1


# ---------------------------------------------------------------------------
# parse_digest_sections
# ---------------------------------------------------------------------------


class TestParseDigestSections:
    def test_empty_string_returns_no_sections(self) -> None:
        assert parse_digest_sections("") == ("", [])

    def test_no_headings_keeps_whole_text_as_preamble(self) -> None:
        preamble, sections = parse_digest_sections("just some prose")
        assert preamble == "just some prose"
        assert sections == []

    def test_splits_by_category_heading(self) -> None:
        digest = "intro\n\n## tech\n- a\n- b\n\n## finance\n- c\n"
        preamble, sections = parse_digest_sections(digest)

        assert preamble == "intro"
        assert [name for name, _ in sections] == ["tech", "finance"]
        assert "a" in sections[0][1]
        assert "c" in sections[1][1]

    def test_first_section_starts_with_its_heading(self) -> None:
        _, sections = parse_digest_sections("## A\nbody a\n## B\nbody b")
        assert sections[0][0] == "A"
        assert sections[0][1].startswith("## A")
        assert sections[1][0] == "B"

    def test_level_one_heading_is_not_a_section(self) -> None:
        """Only level-2 headings count; ``# Top`` is body content."""
        digest = "# Top-level\nintro\n\n## A\nbody"
        preamble, sections = parse_digest_sections(digest)
        assert [name for name, _ in sections] == ["A"]
        # The level-1 heading is preserved in the preamble.
        assert "# Top-level" in preamble


# ---------------------------------------------------------------------------
# build_embed
# ---------------------------------------------------------------------------


class TestBuildEmbed:
    def test_title_and_color_match_branding(self) -> None:
        embed = build_embed("## tech\n- bullet")
        assert embed.title == EMBED_TITLE
        assert embed.title == "📰 Daily News Digest"
        assert embed.color == EMBED_COLOR
        assert embed.color == DEFAULT_EMBED_COLOR

    def test_one_field_per_category(self) -> None:
        digest = "## tech\n- bullet 1\n\n## finance\n- bullet 2"
        embed = build_embed(digest)

        names = [f.name for f in embed.fields]
        assert names == ["tech", "finance"]
        assert all("bullet" in f.value for f in embed.fields)
        assert all(f.inline is False for f in embed.fields)

    def test_preamble_becomes_description(self) -> None:
        digest = "Quick overview paragraph.\n\n## tech\n- bullet"
        embed = build_embed(digest)
        assert "Quick overview paragraph" in embed.description
        assert [f.name for f in embed.fields] == ["tech"]

    def test_no_sections_means_no_fields(self) -> None:
        embed = build_embed("just a sentence")
        assert embed.fields == []
        # Preamble still shows up as description.
        assert embed.description == "just a sentence"

    def test_empty_field_value_is_dropped(self) -> None:
        digest = "## tech\n\n## finance\n- bullet"
        embed = build_embed(digest)
        # The ``tech`` section has no body once the heading is stripped.
        names = [f.name for f in embed.fields]
        assert names == ["finance"]

    def test_long_field_value_is_truncated(self) -> None:
        huge = "x" * 5000
        digest = f"## tech\n{huge}"
        embed = build_embed(digest)
        assert len(embed.fields) == 1
        assert len(embed.fields[0].value) <= 1024

    def test_timestamp_is_passed_through(self) -> None:
        ts = "2026-08-06T08:00:00+00:00"
        embed = build_embed("text", timestamp=ts)
        assert embed.timestamp == ts

    def test_custom_title_and_color(self) -> None:
        embed = build_embed("text", title="Custom", color=0xFFAA00)
        assert embed.title == "Custom"
        assert embed.color == 0xFFAA00


# ---------------------------------------------------------------------------
# run() — happy path
# ---------------------------------------------------------------------------


class TestRunHappyPath:
    """End-to-end happy path: fetch → summarise → deliver."""

    @pytest.mark.asyncio
    async def test_calls_each_stage_once(self) -> None:
        articles = [_article("A", "tech"), _article("B", "finance")]
        digest = "## tech\n- A bullet\n## finance\n- B bullet"
        notifier = _FakeNotifier()

        async def fake_fetch(feeds, since=None):
            return list(articles)

        with (
            patch("news_digest.pipeline.fetch_items", side_effect=fake_fetch),
            patch("news_digest.pipeline.summarize", new=AsyncMock(return_value=digest)),
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(_config())

        assert len(notifier.embeds) == 1
        embed = notifier.embeds[0]
        assert embed.title == EMBED_TITLE
        assert [f.name for f in embed.fields] == ["tech", "finance"]
        assert all(notifier.close_count == 1 for _ in [0])  # closed once

    @pytest.mark.asyncio
    async def test_passes_config_to_fetch_and_summarize(self) -> None:
        cfg = _config()
        digest = "## tech\n- A"
        notifier = _FakeNotifier()

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(return_value=[_article("A")]),
            ) as fetch_mock,
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(return_value=digest),
            ) as sum_mock,
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(cfg)

        # ``fetch_items`` receives the feeds list (which is a pydantic list);
        # we just verify the call happened and the same feeds were passed.
        fetch_mock.assert_awaited_once()
        passed_feeds = fetch_mock.await_args.args[0]
        assert list(passed_feeds) == list(cfg.feeds)

        # ``summarize`` receives articles + ai config.
        sum_mock.assert_awaited_once()
        passed_articles, passed_cfg = sum_mock.await_args.args
        assert len(passed_articles) == 1
        assert passed_articles[0].title == "A"
        assert passed_cfg is cfg.ai

    @pytest.mark.asyncio
    async def test_embed_timestamp_is_set(self) -> None:
        notifier = _FakeNotifier()

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(return_value=[_article("X")]),
            ),
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(return_value="## tech\n- bullet"),
            ),
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(_config())

        embed = notifier.embeds[0]
        assert embed.timestamp is not None
        # ISO-8601 UTC with seconds precision.
        assert "T" in embed.timestamp
        assert "+" in embed.timestamp or "Z" in embed.timestamp


# ---------------------------------------------------------------------------
# run() — empty / failure paths
# ---------------------------------------------------------------------------


class TestRunEdgeCases:
    @pytest.mark.asyncio
    async def test_no_articles_posts_placeholder_embed(self) -> None:
        notifier = _FakeNotifier()

        with (
            patch("news_digest.pipeline.fetch_items", new=AsyncMock(return_value=[])),
            patch("news_digest.pipeline.summarize", new=AsyncMock()) as sum_mock,
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(_config())

        # ``summarize`` is NOT called when there are no articles.
        sum_mock.assert_not_called()
        assert len(notifier.embeds) == 1
        embed = notifier.embeds[0]
        # The placeholder shows up somewhere in description or fields.
        body = (embed.description or "") + "\n" + "\n".join(f.value for f in embed.fields)
        assert "No new articles" in body

    @pytest.mark.asyncio
    async def test_fetch_failure_posts_error_embed(self) -> None:
        notifier = _FakeNotifier()

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(side_effect=RuntimeError("feed boom")),
            ),
            patch("news_digest.pipeline.summarize", new=AsyncMock()) as sum_mock,
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            # ``run`` must swallow the exception.
            await run(_config())

        sum_mock.assert_not_called()
        assert len(notifier.embeds) == 1
        body = (notifier.embeds[0].description or "") + "\n" + "\n".join(
            f.value for f in notifier.embeds[0].fields
        )
        assert "Failed to fetch" in body

    @pytest.mark.asyncio
    async def test_notifier_failure_is_logged_not_raised(self) -> None:
        """A failing notifier must not crash the cycle."""
        notifier = _FakeNotifier(success=False)

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(return_value=[_article("X")]),
            ),
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(return_value="## tech\n- bullet"),
            ),
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            # Must not raise.
            await run(_config())

        assert len(notifier.embeds) == 1

    @pytest.mark.asyncio
    async def test_notifier_factory_error_is_logged_not_raised(self) -> None:
        """A bad config (missing webhook etc.) must not crash the cycle."""

        def _bad_factory(_cfg: Any) -> Any:
            raise RuntimeError("invalid config")

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(return_value=[_article("X")]),
            ),
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(return_value="## tech\n- bullet"),
            ),
            patch(
                "news_digest.pipeline.create_notifier",
                side_effect=_bad_factory,
            ),
        ):
            # Must not raise.
            await run(_config())


# ---------------------------------------------------------------------------
# run() — embed shape
# ---------------------------------------------------------------------------


class TestRunEmbedShape:
    """The embed produced by ``run`` has the expected Discord shape."""

    @pytest.mark.asyncio
    async def test_embed_uses_blurple_color(self) -> None:
        notifier = _FakeNotifier()

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(return_value=[_article("A", "tech")]),
            ),
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(
                    return_value="## tech\n- A bullet",
                ),
            ),
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(_config())

        assert notifier.embeds[0].color == 0x5865F2
        assert notifier.embeds[0].title == "📰 Daily News Digest"

    @pytest.mark.asyncio
    async def test_custom_color_is_respected(self) -> None:
        cfg = _config()
        # Replace default color via override.

        object.__setattr__(
            cfg,
            "discord",
            DiscordConfig(mode="webhook", webhook_url="https://x/y", default_color=0xFF0000),
        )
        notifier = _FakeNotifier()

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(return_value=[_article("A")]),
            ),
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(return_value="## tech\n- bullet"),
            ),
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(cfg)

        # ``create_notifier`` is mocked, so we verify the colour we passed
        # into the embed is the configured colour.
        assert notifier.embeds[0].color == 0xFF0000

    @pytest.mark.asyncio
    async def test_field_inlines_are_false(self) -> None:
        notifier = _FakeNotifier()

        with (
            patch(
                "news_digest.pipeline.fetch_items",
                new=AsyncMock(
                    return_value=[_article("A", "tech"), _article("B", "finance")],
                ),
            ),
            patch(
                "news_digest.pipeline.summarize",
                new=AsyncMock(
                    return_value="## tech\n- A\n## finance\n- B"
                ),
            ),
            patch(
                "news_digest.pipeline.create_notifier",
                return_value=notifier,
            ),
        ):
            await run(_config())

        assert all(f.inline is False for f in notifier.embeds[0].fields)
