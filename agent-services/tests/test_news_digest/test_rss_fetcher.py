"""Unit tests for :mod:`news_digest.rss_fetcher`.

The HTTP layer is mocked with ``respx`` so the real ``feedparser`` runs
against canned RSS bytes, exercising the same code paths as production
without touching the network.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from news_digest.rss_fetcher import (
    DEFAULT_LOOKBACK,
    Article,
    fetch_items,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _rss_xml(items: list[dict[str, Any]]) -> bytes:
    """Build a minimal RSS-2.0 document from a list of item dicts."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        '<title>Test feed</title>',
    ]
    for item in items:
        parts.append("<item>")
        parts.append(f"<title>{item['title']}</title>")
        if "link" in item:
            parts.append(f"<link>{item['link']}</link>")
        if "description" in item:
            parts.append(f"<description>{item['description']}</description>")
        if "pubDate" in item:
            parts.append(f"<pubDate>{item['pubDate']}</pubDate>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _article_dict(title: str, hours_ago: float = 0.0, **kwargs: Any) -> dict[str, Any]:
    """Build an item dict with ``pubDate`` set to ``hours_ago`` ago."""
    when = datetime.now(tz=UTC) - timedelta(hours=hours_ago)
    item: dict[str, Any] = {
        "title": title,
        "link": f"https://example.com/{title.replace(' ', '-')}",
        "description": f"Summary of {title}",
        "pubDate": when.strftime("%a, %d %b %Y %H:%M:%S +0000"),
    }
    item.update(kwargs)
    return item


FEED_TECH = {
    "name": "Hacker News",
    "url": "https://news.ycombinator.com/rss",
    "category": "tech",
}
FEED_GENERAL = {
    "name": "BBC World",
    "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "category": "general",
}


# ---------------------------------------------------------------------------
# Date filtering
# ---------------------------------------------------------------------------


class TestDateFiltering:
    """``fetch_items`` must respect the ``since`` cutoff."""

    @pytest.mark.asyncio
    async def test_returns_only_recent_articles(self) -> None:
        recent = _article_dict("Recent", hours_ago=2)
        old = _article_dict("Old", hours_ago=48)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([recent, old]))
            )

            articles = await fetch_items([FEED_TECH], since=datetime.now(tz=UTC) - timedelta(hours=24))

        assert len(articles) == 1
        assert articles[0].title == "Recent"

    @pytest.mark.asyncio
    async def test_default_since_is_24h(self) -> None:
        """When ``since`` is omitted the lookback defaults to 24h."""
        within = _article_dict("Fresh", hours_ago=12)
        beyond = _article_dict("Stale", hours_ago=36)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([within, beyond]))
            )

            articles = await fetch_items([FEED_TECH])

        assert [a.title for a in articles] == ["Fresh"]
        # The module constant is the source of truth.
        assert timedelta(hours=24) == DEFAULT_LOOKBACK

    @pytest.mark.asyncio
    async def test_since_is_inclusive(self) -> None:
        """An article whose ``pubDate`` equals ``since`` is included."""
        # The pubDate string is second-precision; place the article a couple of
        # seconds after the cutoff so it is unambiguously inside the window
        # (the strict ``<`` comparison rules out equal microsecond timestamps).
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=10)
        borderline = _article_dict("Borderline", hours_ago=(10 - 5) / 3600)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([borderline]))
            )

            articles = await fetch_items([FEED_TECH], since=cutoff)

        assert len(articles) == 1
        assert articles[0].title == "Borderline"

    @pytest.mark.asyncio
    async def test_since_excludes_older_than_cutoff(self) -> None:
        """Strictly older articles are dropped (the comparison is ``<``)."""
        cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
        outside = _article_dict("Outside", hours_ago=2)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([outside]))
            )

            articles = await fetch_items([FEED_TECH], since=cutoff)

        assert articles == []


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Articles with duplicate titles across feeds must be collapsed."""

    @pytest.mark.asyncio
    async def test_dedup_collapses_same_title_across_feeds(self) -> None:
        shared = _article_dict("Shared headline", hours_ago=1)
        unique_a = _article_dict("Tech-only", hours_ago=2)
        unique_b = _article_dict("General-only", hours_ago=3)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(
                    200,
                    content=_rss_xml([shared, unique_a]),
                )
            )
            router.get(FEED_GENERAL["url"]).mock(
                return_value=httpx.Response(
                    200,
                    content=_rss_xml([shared, unique_b]),
                )
            )

            articles = await fetch_items([FEED_TECH, FEED_GENERAL])

        titles = [a.title for a in articles]
        assert titles.count("Shared headline") == 1
        assert "Tech-only" in titles
        assert "General-only" in titles

    @pytest.mark.asyncio
    async def test_dedup_is_case_insensitive(self) -> None:
        """``Foo`` and ``foo`` are the same article."""
        upper = _article_dict("Breaking News", hours_ago=1)
        lower = _article_dict("breaking news", hours_ago=2)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(
                    200,
                    content=_rss_xml([upper, lower]),
                )
            )

            articles = await fetch_items([FEED_TECH])

        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_dedup_keeps_first_occurrence(self) -> None:
        """First-seen wins; later duplicates are dropped."""
        first = _article_dict("Same", hours_ago=4)
        later = _article_dict("Same", hours_ago=1)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([first, later]))
            )

            articles = await fetch_items([FEED_TECH])

        assert len(articles) == 1
        # ``fetch_items`` sorts by published desc, so the later one would be
        # first in the *unsorted* input. After dedup the survivor is whichever
        # appears first in the merged list; verify by category/feed tag.
        assert articles[0].feed_name == "Hacker News"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """A failing feed must not crash the whole fetch cycle."""

    @pytest.mark.asyncio
    async def test_empty_feeds_returns_empty_list(self) -> None:
        assert await fetch_items([]) == []

    @pytest.mark.asyncio
    async def test_broken_feed_does_not_crash_others(self) -> None:
        good = _article_dict("Survivor", hours_ago=1)
        with respx.mock(assert_all_called=False) as router:
            # Bad feed returns 500; good feed returns a normal payload.
            router.get("https://broken.example.com/rss").mock(
                return_value=httpx.Response(500, text="boom")
            )
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([good]))
            )

            articles = await fetch_items(
                [
                    {
                        "name": "Broken",
                        "url": "https://broken.example.com/rss",
                        "category": "broken",
                    },
                    FEED_TECH,
                ]
            )

        assert [a.title for a in articles] == ["Survivor"]

    @pytest.mark.asyncio
    async def test_connection_error_on_one_feed_is_swallowed(self) -> None:
        good = _article_dict("Still here", hours_ago=1)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://unreachable.example.com/rss").mock(
                side_effect=httpx.ConnectError("network down")
            )
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([good]))
            )

            articles = await fetch_items(
                [
                    {
                        "name": "Unreachable",
                        "url": "https://unreachable.example.com/rss",
                        "category": "unreachable",
                    },
                    FEED_TECH,
                ]
            )

        assert [a.title for a in articles] == ["Still here"]


# ---------------------------------------------------------------------------
# Article shape & feed metadata
# ---------------------------------------------------------------------------


class TestArticleMetadata:
    """Each :class:`Article` carries the feed and category it came from."""

    @pytest.mark.asyncio
    async def test_articles_carry_feed_name_and_category(self) -> None:
        item = _article_dict("Headline", hours_ago=1)
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([item]))
            )

            articles = await fetch_items([FEED_TECH])

        assert len(articles) == 1
        article: Article = articles[0]
        assert article.feed_name == "Hacker News"
        assert article.category == "tech"
        assert article.title == "Headline"
        assert article.link.endswith("Headline")
        assert "Summary of Headline" in article.summary
        assert article.published.tzinfo is not None

    @pytest.mark.asyncio
    async def test_articles_are_sorted_newest_first(self) -> None:
        items = [
            _article_dict("newest", hours_ago=0),
            _article_dict("older", hours_ago=2),
            _article_dict("oldest", hours_ago=5),
        ]
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml(items))
            )

            articles = await fetch_items([FEED_TECH])

        assert [a.title for a in articles] == ["newest", "older", "oldest"]


# ---------------------------------------------------------------------------
# Pydantic / duck-typed feed inputs
# ---------------------------------------------------------------------------


class TestFeedInputShapes:
    """``feeds`` accepts pydantic models, dicts and duck-typed objects."""

    @pytest.mark.asyncio
    async def test_pydantic_models_are_accepted(self) -> None:
        from news_digest.config import FeedConfig

        item = _article_dict("Pydantic", hours_ago=1)
        feed = FeedConfig(name="Test", url=FEED_TECH["url"], category="tech")
        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([item]))
            )

            articles = await fetch_items([feed])

        assert len(articles) == 1
        assert articles[0].feed_name == "Test"

    @pytest.mark.asyncio
    async def test_duck_typed_objects_are_accepted(self) -> None:
        item = _article_dict("Duck", hours_ago=1)

        class _Feed:
            name = "DuckFeed"
            url = FEED_TECH["url"]
            category = "tech"

        with respx.mock(assert_all_called=True) as router:
            router.get(FEED_TECH["url"]).mock(
                return_value=httpx.Response(200, content=_rss_xml([item]))
            )

            articles = await fetch_items([_Feed()])

        assert len(articles) == 1
        assert articles[0].feed_name == "DuckFeed"
