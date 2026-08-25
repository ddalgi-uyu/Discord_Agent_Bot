"""Module for synthesizing data from all agents into a single Daily Heartbeat report.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from common.discord_notifier import Embed, EmbedField, Notifier, create_notifier

logger = logging.getLogger(__name__)

async def build_heartbeat_embed(
    currency_data: list[dict[str, Any]],
    etf_data: list[dict[str, Any]],
    news_digest: str,
    discord_config: Any,
    *,
    statuses: list[dict[str, Any]] | None = None,
    previous_run: dict[str, Any] | None = None,
) -> Embed:
    """Consolidate data from all agents into one consolidated daily heartbeat.

    ``statuses`` and ``previous_run`` are optional keyword-only arguments
    that add a ``🩺 Status`` field and a previous-run-aware footer when
    supplied by the unified runner (``scripts.run_all``). The ``/heartbeat``
    bot slash command calls this without those kwargs, preserving the
    pre-existing 4-argument shape.
    """
    # 1. Process Currencies
    curr_lines = []
    for item in currency_data:
        pair = item.get("pair", "Unknown")
        rate = item.get("rate")
        status = "Error" if rate is None else f"{rate:.4f}"
        curr_lines.append(f"- {pair}: {status}")

    curr_text = "\n".join(curr_lines) if curr_lines else "_No currency data available._"

    # 2. Process ETFs
    etf_lines = []
    for item in etf_data:
        symbol = item.get("symbol", "Unknown")
        price = item.get("price")
        status = item.get("status", "Neutral")
        price_text = f"${price:.2f}" if price is not None else "n/a"
        etf_lines.append(f"- {symbol}: {price_text} ({status})")

    etf_text = "\n".join(etf_lines) if etf_lines else "_No asset data available._"

    # 3. News summary (already markdown)
    news_text = news_digest if news_digest else "_No new articles today._"
    # Truncate news to fit in a field
    if len(news_text) > 1000:
        news_text = news_text[:997] + "..."

    fields = [
        EmbedField(name="💵 Currencies", value=curr_text, inline=False),
        EmbedField(name="📈 Assets", value=etf_text, inline=False),
        EmbedField(name="📰 Daily News", value=news_text, inline=False),
    ]

    if statuses:
        status_lines = []
        for s in statuses:
            icon = "✅" if s.get("ok") else "❌"
            err = f" — {s['error']}" if s.get("error") else ""
            items = s.get("items", 0)
            duration = s.get("duration_s", 0.0)
            status_lines.append(
                f"{icon} **{s['label']}** — {duration:.2f}s, "
                f"{items} item{'s' if items != 1 else ''}{err}"
            )
        fields.append(
            EmbedField(
                name="🩺 Status",
                value="\n".join(status_lines) or "_No app status reported._",
                inline=False,
            )
        )

    if previous_run and previous_run.get("timestamp"):
        total_items = sum(a.get("items", 0) for a in previous_run.get("apps", []))
        footer = (
            f"Last successful run: {previous_run['timestamp']} "
            f"({total_items} items processed)"
        )
    else:
        footer = "Last successful run: (first run) · intelligence-hub · heartbeat"

    return Embed(
        title="📅 Daily Market Heartbeat",
        description="Your consolidated intelligence briefing for today.",
        color=int(getattr(discord_config, "default_color", 0x5865F2)),
        fields=fields,
        timestamp=datetime.now(timezone.utc).isoformat(),
        footer=footer,
    )

async def send_heartbeat(
    currency_data: list[dict[str, Any]],
    etf_data: list[dict[str, Any]],
    news_digest: str,
    discord_config: Any,
    *,
    statuses: list[dict[str, Any]] | None = None,
    previous_run: dict[str, Any] | None = None,
) -> bool:
    """Build and send the heartbeat embed via Discord.

    ``statuses`` and ``previous_run`` are forwarded to
    :func:`build_heartbeat_embed` when supplied; otherwise the embed is
    identical to the pre-existing 4-argument shape.
    """
    try:
        embed = await build_heartbeat_embed(
            currency_data,
            etf_data,
            news_digest,
            discord_config,
            statuses=statuses,
            previous_run=previous_run,
        )
        notifier = create_notifier(discord_config)
        ok = await notifier.send_embed(embed)
        await notifier.close()
        return ok
    except Exception as exc:
        logger.error("Heartbeat delivery failed: %s", exc)
        return False

__all__ = ["send_heartbeat", "build_heartbeat_embed"]
