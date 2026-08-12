"""AI summarisation for the news digest pipeline.

Provides :func:`summarize`, a thin async wrapper over either the Anthropic or
OpenAI chat completion API. All articles are sent in a single batch call (one
API round-trip per digest cycle) and the resulting markdown digest is
returned as a string. Provider selection is driven by ``ai_config.provider``.

API keys are read from environment variables:

* ``ANTHROPIC_API_KEY`` — used when ``ai_config.provider == "anthropic"``
* ``OPENAI_API_KEY``     — used when ``ai_config.provider == "openai"``

Failures (missing key, network error, API error) are logged and surfaced as
a human-readable error string instead of being raised. The pipeline can then
post the error message to Discord without crashing the scheduler.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

from news_digest.rss_fetcher import Article

__all__ = ["summarize", "build_prompt"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _group_by_category(articles: Iterable[Article]) -> dict[str, list[Article]]:
    """Group articles by their ``category`` (preserving first-seen order)."""
    grouped: dict[str, list[Article]] = {}
    for article in articles:
        grouped.setdefault(article.category or "uncategorised", []).append(article)
    return grouped


def _format_articles(articles: Iterable[Article]) -> str:
    """Render the article payload sent to the model.
    
    Includes the reach/popularity score in the prompt so the AI can
    prioritise higher-impact stories during summarisation.
    """
    lines: list[str] = []
    for article in articles:
        lines.append(f"- Title: {article.title} (Reach Score: {article.score})")
        if article.link:
            lines.append(f"  Link: {article.link}")
        if article.summary:
            import re
            summary = re.sub(r'<[^>]+>', '', article.summary).replace("\n", " ").strip()
            if len(summary) > 400:
                summary = summary[:400].rstrip() + "…"
            lines.append(f"  Summary: {summary}")
        lines.append(f"  Source: {article.feed_name}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_prompt(
    articles: list[Article],
    *,
    max_bullets_per_category: int,
    max_output_chars: int,
) -> tuple[str, str]:
    """Build ``(system_prompt, user_prompt)`` for the digest call.
    
    The system prompt now includes instructions for Impact Scoring.
    """
    system_prompt = (
        "You are a senior financial analyst. Your task is to produce a a high-signal news digest.\n\n"
        "1. ANALYZE: For every article, evaluate its 'Market Impact Score' from 1 to 10.\n"
        "   - 1-3: Routine news, minor updates.\n"
        "   - 4-7: Meaningful shifts, quarterly reports, sector trends.\n"
        "   - 8-10: Critical events, black swans, major policy changes, or massive price catalysts.\n\n"
        "2. PRIORITIZE: Use the provided 'Reach Score' and your calculated 'Impact Score' to select "
        "the most important stories. Prioritize high-impact stories over high-reach fluff.\n\n"
        f"3. SUMMARIZE: For each category, provide at most {max_bullets_per_category} bullet points. "
        f"Total output must be under {max_output_chars} characters. Format in markdown.\n\n"
        "4. FORMATTING: Use a level-2 markdown heading (``## CategoryName``) for each category. "
        "Start each bullet with ``- ``. If a story is an 'Impact 8+' event, prefix the bullet with '🚨'.\n\n"
        "Do not include any other sections, preamble or commentary."
    )

    if not articles:
        user_prompt = "No articles to summarise."
    else:
        grouped = _group_by_category(articles)
        sections: list[str] = []
        for category, items in grouped.items():
            sections.append(f"## {category}\n\n{_format_articles(items)}")
        user_prompt = "\n\n".join(sections)

    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


async def _summarize_anthropic(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    max_output_chars: int,
) -> str:
    """Single-call Anthropic summarisation.

    Imports the SDK lazily so the rest of the module remains importable in
    environments where ``anthropic`` is not installed (e.g. CI for unrelated
    test suites).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")

    # Imported here so the module can be imported without the SDK present.
    from anthropic import AsyncAnthropic  # type: ignore[import-not-found]

    client = AsyncAnthropic(api_key=api_key)
    try:
        # ``max_tokens`` must be > 0 and large enough to hold the digest;
        # cap at the soft ceiling plus a buffer for the model's framing.
        max_tokens = max(256, max_output_chars + 256)
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    finally:
        await client.close()

    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()


async def _summarize_openai(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    max_output_chars: int,
) -> str:
    """Single-call OpenAI chat completion summarisation."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")

    from openai import AsyncOpenAI  # type: ignore[import-not-found]

    client = AsyncOpenAI(api_key=api_key)
    try:
        # ``max_completion_tokens`` is the modern parameter; older models also
        # accept ``max_tokens``. We pass ``max_tokens`` for maximum
        # compatibility across the GPT-3.5 / GPT-4 / GPT-4o families.
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max(256, max_output_chars + 256),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    finally:
        await client.close()

    if not getattr(response, "choices", None):
        return ""
    return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_provider(config: Any) -> str:
    """Return the provider name from either a dict or a pydantic-style object."""
    if isinstance(config, Mapping):
        return str(config.get("provider", "anthropic"))
    return str(getattr(config, "provider", "anthropic"))


def _resolve_attr(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


async def _summarize_fallback(
    articles: list[Article],
) -> str:
    """Fallback summarizer that uses lead-paragraph extraction.
    
    This method requires no API keys and provides a basic summary by
    extracting the first 200 characters of the top articles.
    """
    summaries = []
    for i, art in enumerate(articles[:5], 1):
        # Use a simple lead extraction. 
        # We ensure no HTML tags are present by treating it as plain text.
        content = art.summary or art.title or "No content available"
        
        # Basic HTML tag stripping if present (e.g. <a href...>)
        import re
        clean_content = re.sub(r'<[^> idea] +[^>]*>', '', content).strip() # Attempt to clean HTML
        # Actually, a simpler regex for any tag
        clean_content = re.sub(r'<[^>]+>', '', content).strip()
        
        snippet = (clean_content[:180] + "...") if len(clean_content) > 180 else clean_content
        
        # Instead of just the snippet, we append the link as a markdown link
        # This solves the HTML tag issue and makes it look professional
        link_text = f" [Source]({art.link})" if art.link else ""
        summaries.append(f"**{i}. {art.title}**\n{snippet}{link_text}")
    
    if not summaries:
        return "No articles found for this category."
        
    return "\n\n".join(summaries)

async def summarize(articles: list[Article], ai_config: Any) -> str:
    """Produce a markdown digest of ``articles``.
    
    Uses specified provider (anthropic/openai) if keys exist, 
    otherwise falls back to a keyless lead-extraction method.
    """
    provider = _resolve_provider(ai_config)
    model = str(_resolve_attr(ai_config, "model", "claude-3-5-sonnet-latest"))
    max_items = int(_resolve_attr(ai_config, "max_items", 10))
    max_bullets = int(_resolve_attr(ai_config, "max_bullets_per_category", 3))
    max_chars = int(_resolve_attr(ai_config, "max_output_chars", 2000))
    
    truncated = list(articles[:max_items])
    system_prompt, user_prompt = build_prompt(
        truncated,
        max_bullets_per_category=max_bullets,
        max_output_chars=max_chars,
    )
    
    logger.info(
        "summarize: provider=%s model=%s articles=%d (truncated from %d)",
        provider,
        model,
        len(truncated),
        len(articles),
    )
    
    try:
        if provider == "openai":
            return await _summarize_openai(
                system_prompt,
                user_prompt,
                model=model,
                max_output_chars=max_chars,
            )
        if provider == "anthropic":
            return await _summarize_anthropic(
                system_prompt,
                user_prompt,
                model=model,
                max_output_chars=max_chars,
            )
        
        # Default to fallback if provider is unknown or explicitly 'fallback'
        return await _summarize_fallback(truncated)
        
    except Exception as exc:
        # If API fails (e.g. missing key), automatically try fallback
        logger.error("API summarization failed, attempting fallback: %s", exc)
        return await _summarize_fallback(truncated)

