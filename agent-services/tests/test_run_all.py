"""Unit tests for ``scripts.run_all`` — primarily the unified ``run_all_once`` runner."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import scripts.run_all as run_all_mod
from common.heartbeat import send_heartbeat as real_send_heartbeat
from news_digest.pipeline import _last_digest_text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_digest_context() -> None:
    """Reset the news-digest ``ContextVar`` between tests so values don't leak."""
    _last_digest_text.set(None)
    yield
    _last_digest_text.set(None)


@pytest.fixture
def tmp_last_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the ``last_run.json`` write path to a tmp dir for the test."""
    path = tmp_path / "last_run.json"
    monkeypatch.setattr(run_all_mod, "_LAST_RUN_PATH", path)
    return path


def _fake_configs() -> tuple[Any, Any, Any]:
    cfg = MagicMock()
    cfg.discord = MagicMock(webhook_url="https://example.com/wh", default_color=0x5865F2)
    cfg.logging = MagicMock(level="INFO", json=False)
    cfg.schedule = MagicMock(cron="0 8 * * *")
    return cfg, cfg, cfg


def _patch_pipeline_runs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    news_digest: str | None = None,
    news_exception: Exception | None = None,
    etf_return: list[dict[str, Any]] | None = None,
    etf_exception: Exception | None = None,
    currency_return: list[dict[str, Any]] | None = None,
    currency_exception: Exception | None = None,
    heartbeat_return: bool = True,
) -> dict[str, Any]:
    """Replace pipeline functions with controllable mocks.

    Returns a dict of references so individual tests can poke at call counts.
    """
    refs: dict[str, Any] = {}

    async def _news_stub(_cfg: Any) -> None:
        refs["news_calls"] = refs.get("news_calls", 0) + 1
        if news_exception is not None:
            raise news_exception
        if news_digest is not None:
            _last_digest_text.set(news_digest)
        return None

    async def _etf_stub(_cfg: Any, **_kw: Any) -> list[dict[str, Any]]:
        refs["etf_calls"] = refs.get("etf_calls", 0) + 1
        if etf_exception is not None:
            raise etf_exception
        return etf_return if etf_return is not None else []

    async def _currency_stub(_cfg: Any, _db: Any, **_kw: Any) -> list[dict[str, Any]]:
        refs["currency_calls"] = refs.get("currency_calls", 0) + 1
        if currency_exception is not None:
            raise currency_exception
        return currency_return if currency_return is not None else []

    heartbeat_mock = AsyncMock(return_value=heartbeat_return)

    monkeypatch.setattr(run_all_mod, "news_run", _news_stub)
    monkeypatch.setattr(run_all_mod, "etf_run", _etf_stub)
    monkeypatch.setattr(run_all_mod, "currency_run", _currency_stub)
    monkeypatch.setattr("common.heartbeat.send_heartbeat", heartbeat_mock)

    # Database needs to be constructible; we don't touch disk.
    fake_db = MagicMock()
    fake_db.close = MagicMock()
    monkeypatch.setattr("common.database.Database", MagicMock(return_value=fake_db))

    refs["heartbeat_mock"] = heartbeat_mock
    return refs


# ---------------------------------------------------------------------------
# _safe_run tests
# ---------------------------------------------------------------------------


async def test_safe_run_returns_status_dict_shape_on_success() -> None:
    async def _coro() -> str:
        return "hello"

    status = await run_all_mod._safe_run("x", _coro())
    assert status["label"] == "x"
    assert status["ok"] is True
    assert status["items"] == 0  # default; callers post-process
    assert status["error"] is None
    assert status["value"] == "hello"
    assert status["duration_s"] >= 0.0


async def test_safe_run_returns_status_dict_shape_on_exception() -> None:
    async def _coro() -> None:
        raise RuntimeError("boom")

    status = await run_all_mod._safe_run("x", _coro())
    assert status["label"] == "x"
    assert status["ok"] is False
    assert status["items"] == 0
    assert status["error"] == "boom"
    assert status["value"] is None


async def test_safe_run_uses_perf_counter_for_duration() -> None:
    async def _slow() -> str:
        await asyncio.sleep(0.05)
        return "done"

    status = await run_all_mod._safe_run("slow", _slow())
    assert status["duration_s"] >= 0.04, f"expected >= 0.04s, got {status['duration_s']}"


# ---------------------------------------------------------------------------
# run_all_once integration-shape tests
# ---------------------------------------------------------------------------


async def test_run_all_once_persists_last_run_json(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    refs = _patch_pipeline_runs(
        monkeypatch,
        news_digest="## Cat1\nbody1\n## Cat2\nbody2\n## Cat3\nbody3\n",
        etf_return=[{"symbol": "VOO", "price": 100.0, "status": "Neutral"}],
        currency_return=[{"pair": "USD/EUR", "rate": 1.08, "status": "OK"}],
        heartbeat_return=True,
    )
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    ok = await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)

    assert ok is True
    assert tmp_last_run.exists()
    payload = json.loads(tmp_last_run.read_text(encoding="utf-8"))
    assert payload["heartbeat_sent"] is True
    assert "timestamp" in payload
    assert "duration_s" in payload
    assert len(payload["apps"]) == 4  # news, etf, currency, heartbeat

    labels = [a["label"] for a in payload["apps"]]
    assert labels == ["news-digest", "etf-signal", "currency-notifier", "heartbeat"]

    # Items: news has 3 headings, etf has 1 symbol, currency has 1 pair, heartbeat=1
    items = {a["label"]: a["items"] for a in payload["apps"]}
    assert items["news-digest"] == 3
    assert items["etf-signal"] == 1
    assert items["currency-notifier"] == 1
    assert items["heartbeat"] == 1


async def test_run_all_once_returns_false_when_heartbeat_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    _patch_pipeline_runs(
        monkeypatch,
        news_digest="## X\nbody",
        etf_return=[{"symbol": "VOO", "price": 100.0, "status": "Neutral"}],
        currency_return=[{"pair": "USD/EUR", "rate": 1.08, "status": "OK"}],
        heartbeat_return=False,  # <-- the key condition
    )
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    ok = await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)
    assert ok is False
    payload = json.loads(tmp_last_run.read_text(encoding="utf-8"))
    assert payload["heartbeat_sent"] is False
    # last_run.json is still written so the *next* run's footer is meaningful.
    assert "timestamp" in payload


async def test_run_all_once_counts_news_items_via_parse_digest_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    """When news_digest sets a 3-heading digest, the persisted items count is 3."""
    digest = "preamble\n## Alpha\n- a\n## Beta\n- b\n## Gamma\n- c\n"
    _patch_pipeline_runs(
        monkeypatch,
        news_digest=digest,
        etf_return=[],
        currency_return=[],
        heartbeat_return=True,
    )
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)

    payload = json.loads(tmp_last_run.read_text(encoding="utf-8"))
    news_app = next(a for a in payload["apps"] if a["label"] == "news-digest")
    assert news_app["items"] == 3


async def test_run_all_once_handles_app_failure_but_still_sends_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    """An app exception must not abort the heartbeat — per-app status reports ok=False."""
    _patch_pipeline_runs(
        monkeypatch,
        news_digest=None,
        etf_exception=RuntimeError("etf down"),
        currency_return=[],
        heartbeat_return=True,
    )
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    ok = await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)

    assert ok is True  # heartbeat still attempted and succeeded
    payload = json.loads(tmp_last_run.read_text(encoding="utf-8"))
    etf_app = next(a for a in payload["apps"] if a["label"] == "etf-signal")
    assert etf_app["ok"] is False
    assert "etf down" in etf_app["error"]


async def test_run_all_once_passes_statuses_and_previous_run_to_send_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    """``send_heartbeat`` must receive ``statuses`` and ``previous_run`` kwargs."""
    # Pre-write a last_run.json so previous_run is non-None.
    tmp_last_run.parent.mkdir(parents=True, exist_ok=True)
    tmp_last_run.write_text(
        json.dumps({
            "timestamp": "2026-08-24T00:00:00+00:00",
            "duration_s": 1.23,
            "apps": [{"label": "heartbeat", "items": 1, "ok": True}],
            "heartbeat_sent": True,
        }),
        encoding="utf-8",
    )

    refs = _patch_pipeline_runs(
        monkeypatch,
        news_digest="## A\n- a",
        etf_return=[],
        currency_return=[],
        heartbeat_return=True,
    )
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)

    refs["heartbeat_mock"].assert_awaited_once()
    kwargs = refs["heartbeat_mock"].call_args.kwargs
    assert "statuses" in kwargs
    assert len(kwargs["statuses"]) == 4  # news, etf, currency, heartbeat
    assert kwargs["statuses"][0]["label"] == "news-digest"
    assert "previous_run" in kwargs
    assert kwargs["previous_run"]["timestamp"] == "2026-08-24T00:00:00+00:00"


async def test_run_all_once_reads_previous_run_when_file_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    """If last_run.json exists from a prior run, ``previous_run`` reflects it."""
    tmp_last_run.parent.mkdir(parents=True, exist_ok=True)
    tmp_last_run.write_text(
        json.dumps({
            "timestamp": "2026-08-23T12:34:56+00:00",
            "apps": [{"items": 9}],
            "heartbeat_sent": True,
        }),
        encoding="utf-8",
    )

    refs = _patch_pipeline_runs(monkeypatch)
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)

    kwargs = refs["heartbeat_mock"].call_args.kwargs
    assert kwargs["previous_run"]["timestamp"] == "2026-08-23T12:34:56+00:00"


async def test_run_all_once_first_run_has_no_previous_run(
    monkeypatch: pytest.MonkeyPatch, tmp_last_run: Path
) -> None:
    """If last_run.json is absent, ``previous_run`` is None — first-run semantics."""
    refs = _patch_pipeline_runs(monkeypatch)
    news_cfg, etf_cfg, currency_cfg = _fake_configs()
    await run_all_mod.run_all_once(news_cfg, etf_cfg, currency_cfg)

    kwargs = refs["heartbeat_mock"].call_args.kwargs
    assert kwargs["previous_run"] is None
