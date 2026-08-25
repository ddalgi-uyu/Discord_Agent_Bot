"""Manager for dynamically updating application configurations.

This module provides utilities to safely read and modify YAML configuration files
at runtime, allowing agents to autonomously add new monitoring targets (feeds/symbols)
without manual user intervention.

The :class:`ConfigManager` is the canonical entry point used by the bot
commands (``/add_feed``). The module-level helpers ``update_yaml_config``,
``add_discovered_feed`` and ``add_discovered_symbol`` are preserved for
back-compat with any external callers and with the discovery-agent code
that used them historically.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ConfigManager:
    """Read/modify a YAML config file with round-trip preservation.

    Parameters
    ----------
    yaml_path:
        Filesystem path to the YAML file. May not yet exist; :meth:`save`
        will create parent directories on demand.
    """

    def __init__(self, yaml_path: Path) -> None:
        self.path = Path(yaml_path)

    def load(self) -> dict[str, Any]:
        """Read and parse the YAML file.

        Returns an empty dict if the file is missing or empty. A malformed
        file raises :class:`yaml.YAMLError` — that is intentional so the
        caller (e.g. ``/add_feed``) can surface a useful error to the user.
        """
        if not self.path.exists():
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        """Write ``data`` back to the YAML file, creating parent dirs.

        Formatting matches the legacy :func:`update_yaml_config`:
        ``default_flow_style=False``, ``sort_keys=False``, ``encoding="utf-8"``.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                default_flow_style=False,
                sort_keys=False,
            )
        logger.info("Saved config to %s", self.path)


# ---------------------------------------------------------------------------
# Legacy module-level helpers (kept for back-compat)
# ---------------------------------------------------------------------------


def update_yaml_config(yaml_path: Path, key: str, value: Any, append: bool = False) -> None:
    """Update a value in a YAML config file.

    If append is True and the target is a list, the value is added to the list.
    Otherwise, the value replaces the existing entry.

    If the YAML file does not yet exist, the function logs an error and
    returns without creating one — callers that want the file to be created
    should use :class:`ConfigManager` directly.
    """
    try:
        if not yaml_path.exists():
            logger.error("Config file not found: %s", yaml_path)
            return

        manager = ConfigManager(yaml_path)
        data = manager.load()
        if append and isinstance(data.get(key), list):
            if value not in data[key]:
                data[key].append(value)
        else:
            data[key] = value
        manager.save(data)
        logger.info("Successfully updated %s: %s", yaml_path.name, key)
    except Exception as exc:
        logger.error("Failed to update config %s: %s", yaml_path, exc)


def add_discovered_feed(feed_url: str, category: str = "general") -> None:
    """Add a discovered RSS feed to the news-digest configuration."""
    yaml_path = Path("C:/Users/cyberspace/Desktop/opencode_project/agent-services/config/news_digest.yaml")
    new_feed = {
        "name": f"Discovered {category}",
        "url": feed_url,
        "category": category,
    }
    update_yaml_config(yaml_path, "feeds", new_feed, append=True)


def add_discovered_symbol(symbol: str) -> None:
    """Add a discovered symbol to the etf-signal configuration."""
    yaml_path = Path("C:/Users/cyberspace/Desktop/opencode_project/agent-services/config/etf_signal.yaml")
    update_yaml_config(yaml_path, "symbols", symbol, append=True)


__all__ = [
    "ConfigManager",
    "update_yaml_config",
    "add_discovered_feed",
    "add_discovered_symbol",
]


