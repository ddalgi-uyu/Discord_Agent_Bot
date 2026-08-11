"""Manager for dynamically updating application configurations.

This module provides utilities to safely read and modify YAML configuration files
at runtime, allowing agents to autonomously add new monitoring targets (feeds/symbols)
without manual user intervention.
"""
from __future__ import annotations

import logging
import yaml
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def update_yaml_config(yaml_path: Path, key: str, value: Any, append: bool = False) -> None:
    """Update a value in a YAML config file.
    
    If append is True and the target is a list, the value is added to the list.
    Otherwise, the value replaces the existing entry.
    """
    try:
        if not yaml_path.exists():
            logger.error("Config file not found: %s", yaml_path)
            return

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if append and isinstance(data.get(key), list):
            if value not in data[key]:
                data[key].append(value)
        else:
            data[key] = value

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            
        logger.info("Successfully updated %s: %s", yaml_path.name, key)
    except Exception as exc:
        logger.error("Failed to update config %s: %s", yaml_path, exc)

def add_discovered_feed(feed_url: str, category: str = "general") -> None:
    """Add a discovered RSS feed to the news-digest configuration."""
    yaml_path = Path("C:/Users/cyberspace/Desktop/opencode_project/agent-services/config/news_digest.yaml")
    new_feed = {
        "name": f"Discovered {category}",
        "url": feed_url,
        "category": category
    }
    update_yaml_config(yaml_path, "feeds", new_feed, append=True)

def add_discovered_symbol(symbol: str) -> None:
    """Add a discovered symbol to the etf-signal configuration."""
    yaml_path = Path("C:/Users/cyberspace/Desktop/opencode_project/agent-services/config/etf_signal.yaml")
    update_yaml_config(yaml_path, "symbols", symbol, append=True)


