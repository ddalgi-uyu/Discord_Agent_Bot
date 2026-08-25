"""Unit tests for ``common.heartbeat``."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from common.heartbeat import build_heartbeat_embed


def _discord_config(color: int = 0x5865F2) -> Any:
    """Minimal stand-in for the Pydantic ``DiscordConfig`` the embed builder expects."""
    return SimpleNamespace(default_color=color)


def _currency_data(*pairs: tuple[str, float | None]) -> list[dict[str, Any]]:
    return [{"pair": p, "rate": r, "status": "OK" if r is not None else "Error"} for p, r in pairs]


def _etf_data(*symbols: tuple[str, float | None, str]) -> list[dict[str, Any]]:
    return [{"symbol": s, "price": p, "status": st} for s, p, st in symbols]


async def test_build_heartbeat_embed_includes_status_field_when_provided() -> None:
    statuses = [
        {"label": "news-digest", "ok": True, "duration_s": 4.21, "items": 3, "error": None, "value": "digest"},
        {"label": "etf-signal", "ok": False, "duration_s": 0.84, "items": 5, "error": "boom", "value": None},
    ]
    embed = await build_heartbeat_embed(
        _currency_data(("USD/EUR", 1.08)),
        _etf_data(("VOO", 420.0, "Neutral")),
        news_digest="",
        discord_config=_discord_config(),
        statuses=statuses,
    )
    field_names = [f.name for f in embed.fields]
    assert "🩺 Status" in field_names
    status_field = next(f for f in embed.fields if f.name == "🩺 Status")
    assert "✅" in status_field.value
    assert "❌" in status_field.value
    assert "news-digest" in status_field.value
    assert "4.21s" in status_field.value
    assert "3 items" in status_field.value
    assert "etf-signal" in status_field.value
    assert "— boom" in status_field.value
    assert "5 items" in status_field.value


async def test_build_heartbeat_embed_no_status_field_when_omitted() -> None:
    """Default behaviour (no statuses) must not add the Status field.

    ``bot.py /heartbeat`` calls ``build_heartbeat_embed`` with only the four
    positional args — this test guards that contract.
    """
    embed = await build_heartbeat_embed(
        _currency_data(),
        _etf_data(),
        news_digest="",
        discord_config=_discord_config(),
    )
    assert "🩺 Status" not in [f.name for f in embed.fields]


async def test_build_heartbeat_embed_status_singular_item_grammar() -> None:
    """``items == 1`` must render as ``1 item`` (not ``1 items``)."""
    statuses = [{"label": "x", "ok": True, "duration_s": 0.1, "items": 1, "error": None, "value": None}]
    embed = await build_heartbeat_embed(
        _currency_data(),
        _etf_data(),
        news_digest="",
        discord_config=_discord_config(),
        statuses=statuses,
    )
    status_field = next(f for f in embed.fields if f.name == "🩺 Status")
    assert "1 item " in status_field.value or status_field.value.endswith("1 item")
    assert "1 items" not in status_field.value


async def test_build_heartbeat_embed_footer_shows_previous_run_timestamp() -> None:
    previous_run = {
        "timestamp": "2026-08-25T00:00:00+00:00",
        "apps": [{"items": 7}, {"items": 3}],
    }
    embed = await build_heartbeat_embed(
        _currency_data(),
        _etf_data(),
        news_digest="",
        discord_config=_discord_config(),
        previous_run=previous_run,
    )
    assert embed.footer is not None
    assert "Last successful run: 2026-08-25T00:00:00+00:00 (10 items processed)" in embed.footer


async def test_build_heartbeat_embed_footer_first_run_when_no_previous_run() -> None:
    """No ``previous_run`` arg → footer reports first-run state."""
    embed = await build_heartbeat_embed(
        _currency_data(),
        _etf_data(),
        news_digest="",
        discord_config=_discord_config(),
    )
    assert embed.footer is not None
    assert "Last successful run: (first run)" in embed.footer


async def test_build_heartbeat_embed_combined_status_and_footer() -> None:
    """Both kwargs together produce both the Status field and the prior-run footer."""
    embed = await build_heartbeat_embed(
        _currency_data(),
        _etf_data(),
        news_digest="",
        discord_config=_discord_config(),
        statuses=[{"label": "x", "ok": True, "duration_s": 0.5, "items": 2, "error": None, "value": None}],
        previous_run={"timestamp": "2026-08-24T00:00:00+00:00", "apps": [{"items": 5}]},
    )
    field_names = [f.name for f in embed.fields]
    assert "🩺 Status" in field_names
    assert embed.footer is not None
    assert "2026-08-24T00:00:00+00:00 (5 items processed)" in embed.footer
