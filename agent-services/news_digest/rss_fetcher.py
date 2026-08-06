"""RSS / Atom feed fetcher for the news digest pipeline.

Fetches a list of feeds concurrently, parses each one with ``feedparser``,
filters entries older than ``since`` (default: 24h ago), and deduplicates by
title (case-insensitive) across feeds. Per-feed failures are logged and
swallowed so one bad feed does not abort the whole cycle.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser  # type: ignore[import-untyped]

from common.http_client import create_async_client

__all__ = ["Article", "fetch_items", "DEFAULT_LOOKBACK"]

logger = logging.getLogger(__name__)

# Default lookback window when ``since`` is not provided.
DEFAULT_LOOKBACK: timedelta = timedelta(hours=24)


@dataclass(frozen=True)
class Article:
    """A single normalised news article.

    ``published`` is the timezone-aware UTC datetime of the entry; entries
    without a parseable date get the sentinel :data:`datetime.min` (UTC) so
    that ordering remains stable and the filter below is total.
    """

    title: str
    link: str
    summary: str
    published: datetime
    feed_name: str
    category: str


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _entry_datetime(entry: Any) -> datetime | None:
    """Best-effort: extract a timezone-aware UTC datetime from a feedparser entry.

    Prefers ``published_parsed`` (a ``time.struct_time`` interpreted as UTC by
    feedparser) and falls back to parsing the RFC-822 ``published`` string.
    Returns ``None`` when neither is available.
    """
    parsed_struct = entry.get("published_parsed") if hasattr(entry, "get") else None
    if parsed_struct is not None:
        # calendar.timegm treats the tuple as UTC, unlike time.mktime which
        # applies the local timezone. This is what we want because feedparser
        # normalises dates to UTC.
        epoch = calendar.timegm(parsed_struct)
        return datetime.fromtimestamp(epoch, tz=UTC)

    raw = entry.get("published") if hasattr(entry, "get") else None
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except (TypeError, ValueError):
            return None

    return None


def _entry_text(entry: Any, *keys: str) -> str:
    """Read the first non-empty string among ``keys`` from a feedparser entry.

    ``feedparser`` exposes both top-level fields and ``*_detail`` dicts; this
    helper picks the first available value with a sensible fallback so we never
    end up with ``None`` strings embedded in :class:`Article`.
    """
    for key in keys:
        value = entry.get(key) if hasattr(entry, "get") else None
        if value:
            return str(value).strip()
    return ""


# ---------------------------------------------------------------------------
# Single-feed parsing
# ---------------------------------------------------------------------------


async def _parse_feed(
    client: Any,
    feed: dict[str, str],
    since: datetime,
) -> list[Article]:
    """Fetch and parse a single feed. Returns ``[]`` on any failure.

    ``feed`` is a dict with ``name``, ``url`` and ``category``. Errors are
    logged but never raised — the caller relies on this to keep going when
    one feed is broken.
    """
    name = feed.get("name", "")
    url = feed.get("url", "")
    category = feed.get("category", "")

    try:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
    except Exception as exc:  # noqa: BLE001 — per-feed isolation
        logger.warning(
            "fetch_items: failed to download feed %s (%s): %s",
            name,
            url,
            exc,
        )
        return []

    try:
        parsed = feedparser.parse(content)
    except Exception as exc:  # noqa: BLE001 — feedparser rarely raises, but be safe
        logger.warning("fetch_items: failed to parse feed %s: %s", name, exc)
        return []

    articles: list[Article] = []
    for entry in parsed.entries:
        published = _entry_datetime(entry) or datetime.min.replace(tzinfo=UTC)
        if published < since:
            continue

        title = _entry_text(entry, "title")
        if not title:
            # An article without a title cannot be deduplicated reliably; skip.
            continue

        articles.append(
            Article(
                title=title,
                link=_entry_text(entry, "link"),
                summary=_entry_text(entry, "summary", "description"),
                published=published,
                feed_name=name,
                category=category,
            )
        )

    logger.debug("fetch_items: feed %s produced %d articles", name, len(articles))
    return articles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _normalise_feeds(feeds: Any) -> list[dict[str, str]]:
    """Coerce ``feeds`` into ``list[dict[str, str]]``.

    Accepts pydantic models (via ``.model_dump()``), plain dicts, or any
    object exposing ``name``/``url``/``category`` attributes.
    """
    normalised: list[dict[str, str]] = []
    for feed in feeds:
        if isinstance(feed, dict):
            normalised.append(
                {
                    "name": str(feed.get("name", "")),
                    "url": str(feed.get("url", "")),
                    "category": str(feed.get("category", "")),
                }
            )
        elif hasattr(feed, "model_dump"):
            data = feed.model_dump()
            normalised.append(
                {
                    "name": str(data.get("name", "")),
                    "url": str(data.get("url", "")),
                    "category": str(data.get("category", "")),
                }
            )
        else:
            normalised.append(
                {
                    "name": str(getattr(feed, "name", "")),
                    "url": str(getattr(feed, "url", "")),
                    "category": str(getattr(feed, "category", "")),
                }
            )
    return normalised


def _dedupe(articles: list[Article]) -> list[Article]:
    """Drop later occurrences of the same title (case-insensitive)."""
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = article.title.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique


async def fetch_items(
    feeds: Any,
    since: datetime | None = None,
) -> list[Article]:
    """Fetch, filter and deduplicate articles from ``feeds``.

    Parameters
    ----------
    feeds:
        Iterable of feed subscriptions. Each entry must expose ``name``,
        ``url`` and ``category``. Pydantic models, dicts and duck-typed
        objects are all accepted.
    since:
        Lower-bound (inclusive) cutoff for ``published``. Defaults to
        ``now(UTC) - DEFAULT_LOOKBACK`` (24h ago).

    Returns
    -------
    list[Article]
        Unique recent articles, sorted by ``published`` descending. The list
        is empty when ``feeds`` is empty or every feed fails.
    """
    feed_list = _normalise_feeds(feeds)
    if not feed_list:
        return []

    cutoff = since if since is not None else datetime.now(tz=UTC) - DEFAULT_LOOKBACK
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)

    client = create_async_client()
    try:
        # Gather results; gather() never short-circuits because _parse_feed
        # already swallows per-feed errors and returns [] on failure.
        per_feed_results = await asyncio.gather(
            *(_parse_feed(client, feed, cutoff) for feed in feed_list),
        )
    finally:
        await client.aclose()

    combined: list[Article] = [a for batch in per_feed_results for a in batch]
    unique = _dedupe(combined)
    unique.sort(key=lambda a: a.published, reverse=True)
    logger.info(
        "fetch_items: %d articles from %d feeds (%d after dedup)",
        len(combined),
        len(feed_list),
        len(unique),
    )
    return unique
