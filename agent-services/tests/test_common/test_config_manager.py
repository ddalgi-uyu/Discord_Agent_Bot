"""Tests for ``common.config_manager.ConfigManager`` and legacy helpers."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from common.config_manager import (
    ConfigManager,
    add_discovered_feed,
    add_discovered_symbol,
    update_yaml_config,
)


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "missing.yaml")
    assert manager.load() == {}


def test_load_returns_empty_dict_when_file_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert ConfigManager(p).load() == {}


def test_load_returns_parsed_dict(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\nb: [x, y]\n", encoding="utf-8")
    assert ConfigManager(p).load() == {"a": 1, "b": ["x", "y"]}


def test_save_writes_yaml_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    data = {"feeds": [{"url": "https://x", "name": "X"}], "logging": {"level": "INFO"}}
    ConfigManager(p).save(data)
    assert ConfigManager(p).load() == data


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "deep" / "config.yaml"
    ConfigManager(p).save({"k": "v"})
    assert p.exists()
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == {"k": "v"}


def test_save_preserves_insertion_order(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    ordered = {"z": 1, "a": 2, "m": 3}
    ConfigManager(p).save(ordered)
    keys = list(yaml.safe_load(p.read_text(encoding="utf-8")).keys())
    assert keys == ["z", "a", "m"]


def test_legacy_update_yaml_config_still_works(tmp_path: Path) -> None:
    """Back-compat: existing module-level helper still functions after the refactor."""
    p = tmp_path / "config.yaml"
    p.write_text("feeds: []\n", encoding="utf-8")
    update_yaml_config(p, "feeds", {"url": "u"}, append=True)
    assert ConfigManager(p).load() == {"feeds": [{"url": "u"}]}


def test_legacy_update_yaml_config_replaces_when_append_false(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("mode: old\n", encoding="utf-8")
    update_yaml_config(p, "mode", "new", append=False)
    assert ConfigManager(p).load() == {"mode": "new"}


def test_legacy_update_yaml_config_handles_missing_file(tmp_path: Path) -> None:
    """Missing file is logged and skipped, not raised — matches legacy behaviour."""
    p = tmp_path / "does-not-exist.yaml"
    update_yaml_config(p, "x", 1)  # must not raise
    assert not p.exists()


def test_add_discovered_feed_writes_correct_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``add_discovered_feed`` is hard-coded to a specific YAML path; redirect it."""
    target = tmp_path / "news_digest.yaml"
    target.write_text("feeds: []\n", encoding="utf-8")
    # Replace the hard-coded Path inside the helper so the test is hermetic.
    monkeypatch.setattr(
        "common.config_manager.Path",
        lambda p: target if "news_digest.yaml" in str(p) else Path(p),
    )
    add_discovered_feed("https://example.com/rss", category="tech")
    # The lambda above is fragile; re-load with the real Path to confirm content.
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["feeds"][0]["url"] == "https://example.com/rss"
    assert data["feeds"][0]["category"] == "tech"


def test_add_discovered_symbol_writes_correct_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "etf_signal.yaml"
    target.write_text("symbols: [VOO]\n", encoding="utf-8")
    monkeypatch.setattr(
        "common.config_manager.Path",
        lambda p: target if "etf_signal.yaml" in str(p) else Path(p),
    )
    add_discovered_symbol("QQQ")
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert "QQQ" in data["symbols"]
    assert "VOO" in data["symbols"]  # original preserved
