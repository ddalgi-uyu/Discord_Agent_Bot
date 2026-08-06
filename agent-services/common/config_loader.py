"""Application configuration loader.

Combines a YAML configuration file with optional ``.env`` overlay and
environment-variable overrides, then validates the merged result against a
pydantic model.

Environment variable naming convention::

    {APP_NAME_UPPER}_{KEY_PATH}

Nested keys use a double underscore (``__``) separator. For example, given
``app_name="news-digest"``:

* ``NEWS_DIGEST_AI_PROVIDER`` overrides ``ai.provider``
* ``NEWS_DIGEST_DISCORD__MODE`` overrides ``discord.mode``
* ``NEWS_DIGEST_DISCORD__DEFAULT_COLOR`` overrides ``discord.default_color``

Values are coerced to ``bool`` / ``int`` / ``float`` when possible before being
written into the merged dictionary; pydantic performs the final validation.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError

from common.exceptions import ConfigError

# ``LoggingConfig.json`` intentionally shadows the deprecated ``BaseModel.json``
# method. The field name is part of the public schema so we cannot rename it;
# silence the cosmetic ``UserWarning`` pydantic emits at class-build time.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "json" in "LoggingConfig" shadows an attribute in parent "BaseModel"',
    category=UserWarning,
)

__all__ = [
    "DiscordConfig",
    "LoggingConfig",
    "ScheduleConfig",
    "AppConfig",
    "load_config",
]


class DiscordConfig(BaseModel):
    """Discord delivery configuration."""

    mode: Literal["webhook", "bot"] = "webhook"
    webhook_url: str | None = None
    bot_token: str | None = None
    channel_id: int | None = None
    default_color: int = 0x5865F2


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(protected_namespaces=())

    level: str = "INFO"
    json: bool = False
    file: Path | None = None


class ScheduleConfig(BaseModel):
    """Scheduling configuration."""

    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str = "UTC"


class AppConfig(BaseModel):
    """Top-level application configuration."""

    app_name: str
    discord: DiscordConfig = DiscordConfig()
    logging: LoggingConfig = LoggingConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    timezone: str = "UTC"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_env_value(raw: str) -> Any:
    """Best-effort coercion of an environment-variable string to a Python value.

    Returns ``bool`` for ``"true"`` / ``"false"`` (case-insensitive), ``int``
    for integer literals (including hex ``0x...``, octal ``0o...``, binary
    ``0b...``), ``float`` for decimal floats, otherwise the original string.
    """
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"

    # base=0 lets int() parse 0x..., 0o..., 0b... prefixes transparently.
    try:
        return int(raw, 0)
    except ValueError:
        pass

    try:
        return float(raw)
    except ValueError:
        pass

    return raw


def _set_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    """Set ``target[path[0]][path[1]]...[path[-1]] = value``, creating dicts
    along the way. Existing non-dict intermediate values are replaced.
    """
    cursor: dict[str, Any] = target
    for key in path[:-1]:
        existing = cursor.get(key)
        if not isinstance(existing, dict):
            existing = {}
            cursor[key] = existing
        cursor = existing
    cursor[path[-1]] = value


def _apply_env_overlay(merged: dict[str, Any], app_name: str) -> None:
    """Overlay ``os.environ`` values whose key starts with ``{APP_NAME_UPPER}_``
    onto ``merged`` in place. Nested keys are separated by ``__``.

    Hyphens in ``app_name`` are normalized to underscores so that
    ``app_name="news-digest"`` matches env vars like ``NEWS_DIGEST_*``.
    """
    prefix = app_name.upper().replace("-", "_") + "_"
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        remainder = env_key[len(prefix):]
        if not remainder:
            # Bare prefix (e.g. ``NEWS_DIGEST_``) is not a meaningful key.
            continue
        parts = [segment.lower() for segment in remainder.split("__") if segment]
        if not parts:
            continue
        _set_nested(merged, parts, _coerce_env_value(env_value))


def _read_yaml(yaml_path: Path) -> dict[str, Any]:
    """Load YAML from ``yaml_path`` and return as ``dict``. Empty files produce
    an empty dict. Raises ``ConfigError`` for missing or malformed input.
    """
    try:
        text = yaml_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"Config YAML not found: {yaml_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read config YAML {yaml_path}: {exc}") from exc

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {yaml_path}: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"Top-level YAML in {yaml_path} must be a mapping, got {type(loaded).__name__}"
        )
    return loaded


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(
    app_name: str,
    yaml_path: Path,
    env_path: Path | None = None,
    config_class: type[AppConfig] = AppConfig,
) -> AppConfig:
    """Load and validate application configuration.

    1. Read ``yaml_path`` into a dict.
    2. If ``env_path`` is provided and exists, load it via ``python-dotenv``
       (values populate ``os.environ`` without overwriting existing entries).
    3. Overlay ``{APP_NAME_UPPER}_*`` environment variables onto the dict,
       using ``__`` for nested keys.
    4. Validate the merged dict via ``config_class.model_validate``.

    A missing ``env_path`` is silently ignored. Missing YAML, malformed YAML,
    and pydantic validation errors are raised as :class:`ConfigError`.
    """
    if not isinstance(yaml_path, Path):
        yaml_path = Path(yaml_path)

    merged = _read_yaml(yaml_path)

    if env_path is not None:
        env_path = Path(env_path)
        if env_path.exists():
            # python-dotenv does not overwrite existing os.environ values by
            # default, which means an already-exported shell variable wins
            # over the same key defined in .env.
            load_dotenv(env_path, override=False)

    _apply_env_overlay(merged, app_name)

    try:
        return config_class.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            f"Configuration validation failed for '{app_name}': {exc}"
        ) from exc
