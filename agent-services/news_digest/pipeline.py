"""End-to-end pipeline for the news digest service.

Glues the three pipeline stages together:

1. :func:`news_digest.rss_fetcher.fetch_items` — collect recent articles.
2. :func:`news_digest.summarizer.summarize`   — produce a markdown digest.
3. :func:`common.discord_notifier.create_notifier` + ``send_embed`` — deliver
   the digest as a Discord rich embed with one field per category.

The function is the only public entry point used by :mod:`news_digest.__main__`
and by integration tests.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from common.discord_notifier import Embed, EmbedField, create_notifier
from news_digest.config import NewsDigestConfig
from news_digest.rss_fetcher import Article, fetch_items
from news_digest.summarizer import summarize

__all__ = ["run", "build_embed", "parse_digest_sections"]

logger = logging.getLogger(__name__)

EMBED_TITLE = "📰 Daily News Digest"
EMBED_COLOR = 0x5865F2

# Matches a level-2 markdown heading ``## <name>`` at the start of a line.
# Category names are anything up to the line end; we trim whitespace.
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Discord embeds cap field values at 1024 characters; we leave headroom.
_MAX_FIELD_VALUE_CHARS = 1000


def parse_digest_sections(digest: str) -> tuple[str, list[tuple[str, str]]]:
    """Split ``digest`` into ``(preamble, [(category, body), ...])``.

    The preamble is whatever appears before the first ``## Heading`` (if any).
    Each body runs from the heading line (inclusive) up to (but not including)
    the next heading, or to the end of the digest. Lines that look like
    headings but are not actually a category (e.g. ``# Top-level``) are
    preserved as plain content in the preceding section.
    """
    if not digest:
        return "", []

    matches = list(_SECTION_RE.finditer(digest))
    if not matches:
        return digest.strip(), []

    preamble = digest[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(digest)
        name = match.group(1).strip()
        body = digest[start:end].strip()
        sections.append((name, body))

    return preamble, sections


def _truncate(text: str, limit: int = _MAX_FIELD_VALUE_CHARS) -> str:
    """Trim ``text`` to ``limit`` characters, appending an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_embed(
    digest: str,
    *,
    title: str = EMBED_TITLE,
    color: int = EMBED_COLOR,
    timestamp: str | None = None,
    footer: str = "agent-services · news-digest",
) -> Embed:
    """Convert a markdown digest into a Discord :class:`Embed`.

    The preamble becomes the embed description; each ``## Category`` section
    becomes a separate field named after the category.
    """
    preamble, sections = parse_digest_sections(digest)

    fields: list[EmbedField] = []
    for name, body in sections:
        # Strip the leading ``## Heading`` line from the body so the field
        # value only contains the bullet list.
        body_lines = body.splitlines()
        if body_lines and body_lines[0].lstrip().startswith("##"):
            body_lines = body_lines[1:]
        body_text = "\n".join(body_lines).strip()
        if not body_text:
            continue
        fields.append(
            EmbedField(
                name=name[:256],  # Discord field-name limit
                value=_truncate(body_text),
                inline=False,
            )
        )

    description = _truncate(preamble) if preamble else ""
    return Embed(
        title=title,
        description=description,
        color=color,
        fields=fields,
        timestamp=timestamp,
        footer=footer,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _post_embed(config: NewsDigestConfig, embed: Embed) -> bool:
    """Send ``embed`` via the configured Discord notifier.

    Returns ``True`` on success, ``False`` otherwise. Configuration errors
    (missing webhook URL, unknown mode) are logged and treated as a non-fatal
    failure so the scheduler keeps running.
    """
    try:
        notifier = create_notifier(config.discord)
    except Exception as exc:  # noqa: BLE001 — surface as soft failure
        logger.error(
            "run: failed to build notifier from config: %s",
            exc,
        )
        return False

    try:
        ok = await notifier.send_embed(embed)
    finally:
        await notifier.close()
    if not ok:
        logger.error("run: Discord rejected the digest embed")
    return ok


async def run(config: NewsDigestConfig) -> None:
    """Run one news-digest cycle: fetch → summarise → deliver.

    Designed to be called both standalone (``--once``) and from the
    scheduler. Logs each step and never raises — Discord delivery failures
    are logged and returned; fetch / summarisation errors are surfaced
    through the rendered embed body so the operator sees them in the
    channel.
    """
    logger.info("run: starting news digest cycle")
    timestamp = datetime.now(tz=UTC).isoformat(timespec="seconds")

    # ------------------------------------------------------------------ fetch
    try:
        articles: list[Article] = await fetch_items(config.feeds)
    except Exception as exc:  # noqa: BLE001 — bulletproof cycle
        logger.error("run: fetch_items failed: %s", exc, exc_info=True)
        await _post_embed(
            config,
            build_embed(
                f"❌ Failed to fetch feeds: {exc}",
                timestamp=timestamp,
            ),
        )
        return

    if not articles:
        logger.info("run: no new articles in the lookback window")
        await _post_embed(
            config,
            build_embed(
                "_No new articles in the last 24 hours._",
                timestamp=timestamp,
            ),
        )
        return

    # -------------------------------------------------------------- summarise
    # Priority Sorting: Sort articles by their perceived popularity/importance before summarising.
    # In a full implementation, this would use a 'score' field from the discovery agent.
    # For now, we treat the order from the RSS fetcher as the baseline priority.
    digest = await summarize(articles, config.ai)
    embed = build_embed(
        digest,
        color=getattr(config.discord, "default_color", EMBED_COLOR),
        timestamp=timestamp,
    )
    await _post_embed(config, embed)
    logger.info(
        "run: cycle complete (articles=%d, fields=%d)",
        len(articles),
        len(embed.fields),
    )
