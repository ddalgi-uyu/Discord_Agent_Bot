"""Unit tests for ``common.config_loader``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from common.config_loader import (
    AppConfig,
    DiscordConfig,
    LoggingConfig,
    ScheduleConfig,
    load_config,
)
from common.exceptions import ConfigError

# Env-var prefixes the loader recognizes. Tests must keep these clean between
# runs to avoid leakage from the host shell.
PREFIXES_TO_SCRUB = ("NEWS_DIGEST_", "TESTAPP_", "MYAPP_")


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any leftover env vars from previous runs or the host shell.

    ``monkeypatch`` only restores keys it has touched, so an autouse scrub
    keeps every test deterministic regardless of how many vars it sets itself.
    """
    for key in list(os.environ.keys()):
        if any(key.startswith(p) for p in PREFIXES_TO_SCRUB):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _write_env(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Basic YAML loading & pydantic validation
# ---------------------------------------------------------------------------


class TestBasicYamlLoading:
    """Happy-path YAML loading without any environment overrides."""

    def test_loads_minimal_yaml(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: my-app\n",
        )
        cfg = load_config("my-app", yaml_path)

        assert isinstance(cfg, AppConfig)
        assert cfg.app_name == "my-app"
        # Defaults from sub-models
        assert cfg.discord.mode == "webhook"
        assert cfg.discord.default_color == 0x5865F2
        assert cfg.logging.level == "INFO"
        assert cfg.logging.json is False
        assert cfg.logging.file is None
        assert cfg.schedule.timezone == "UTC"
        assert cfg.timezone == "UTC"

    def test_loads_full_yaml(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            (
                "app_name: full-app\n"
                "timezone: Asia/Taipei\n"
                "discord:\n"
                "  mode: bot\n"
                "  bot_token: secret-token\n"
                "  channel_id: 1234567890\n"
                "  default_color: 16711680\n"
                "logging:\n"
                "  level: DEBUG\n"
                "  json: true\n"
                "  file: /var/log/app.log\n"
                "schedule:\n"
                "  cron: '*/5 * * * *'\n"
                "  timezone: Europe/London\n"
            ),
        )
        cfg = load_config("full-app", yaml_path)

        assert cfg.app_name == "full-app"
        assert cfg.timezone == "Asia/Taipei"
        assert cfg.discord.mode == "bot"
        assert cfg.discord.bot_token == "secret-token"
        assert cfg.discord.channel_id == 1234567890
        assert cfg.discord.default_color == 16711680
        assert cfg.logging.level == "DEBUG"
        assert cfg.logging.json is True
        assert cfg.logging.file == Path("/var/log/app.log")
        assert cfg.schedule.cron == "*/5 * * * *"
        assert cfg.schedule.timezone == "Europe/London"

    def test_yaml_default_color_accepts_hex_literal(
        self, tmp_path: Path
    ) -> None:
        """PyYAML parses ``0xRRGGBB`` as int; pydantic should preserve it."""
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: x\ndiscord:\n  default_color: 0xFF0000\n",
        )
        cfg = load_config("x", yaml_path)
        assert cfg.discord.default_color == 0xFF0000

    def test_string_path_argument_is_accepted(
        self, tmp_path: Path
    ) -> None:
        """``yaml_path`` declared as ``Path`` should also accept ``str``."""
        yaml_path = _write_yaml(tmp_path / "config.yaml", "app_name: s\n")
        # Pass as ``str`` to verify the loader coerces.
        cfg = load_config("s", str(yaml_path))  # type: ignore[arg-type]
        assert cfg.app_name == "s"


# ---------------------------------------------------------------------------
# .env overlay
# ---------------------------------------------------------------------------


class TestEnvOverlay:
    """Environment variables override YAML values when the prefix matches."""

    def test_env_var_overrides_yaml_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\ndiscord:\n  mode: webhook\n",
        )
        monkeypatch.setenv("MYAPP_DISCORD__MODE", "bot")

        cfg = load_config("myapp", yaml_path)

        assert cfg.discord.mode == "bot"

    def test_nested_override_via_double_underscore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            (
                "app_name: myapp\n"
                "logging:\n"
                "  level: INFO\n"
                "  json: false\n"
                "schedule:\n"
                "  cron: '0 * * * *'\n"
            ),
        )
        monkeypatch.setenv("MYAPP_LOGGING__LEVEL", "DEBUG")
        monkeypatch.setenv("MYAPP_LOGGING__JSON", "true")
        monkeypatch.setenv("MYAPP_SCHEDULE__CRON", "*/10 * * * *")

        cfg = load_config("myapp", yaml_path)

        assert cfg.logging.level == "DEBUG"
        assert cfg.logging.json is True
        assert cfg.schedule.cron == "*/10 * * * *"

    def test_hyphenated_app_name_uses_underscore_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``app_name='news-digest'`` should match env vars prefixed ``NEWS_DIGEST_``."""
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: news-digest\nlogging:\n  level: INFO\n",
        )
        monkeypatch.setenv("NEWS_DIGEST_LOGGING__LEVEL", "WARNING")

        cfg = load_config("news-digest", yaml_path)

        assert cfg.logging.level == "WARNING"

    def test_env_var_int_coercion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\ndiscord:\n  channel_id: 0\n",
        )
        monkeypatch.setenv("MYAPP_DISCORD__CHANNEL_ID", "9876543210")

        cfg = load_config("myapp", yaml_path)

        assert cfg.discord.channel_id == 9876543210
        assert isinstance(cfg.discord.channel_id, int)

    def test_env_var_bool_coercion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\nlogging:\n  json: false\n",
        )
        monkeypatch.setenv("MYAPP_LOGGING__JSON", "TRUE")

        cfg = load_config("myapp", yaml_path)

        assert cfg.logging.json is True

    def test_env_var_hex_int_coercion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\ndiscord:\n  default_color: 0\n",
        )
        monkeypatch.setenv("MYAPP_DISCORD__DEFAULT_COLOR", "0x00FF00")

        cfg = load_config("myapp", yaml_path)

        assert cfg.discord.default_color == 0x00FF00

    def test_unrelated_env_vars_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\ndiscord:\n  mode: webhook\n",
        )
        # Wrong prefix — must be ignored.
        monkeypatch.setenv("OTHERAPP_DISCORD__MODE", "bot")
        # Bare prefix with no key — must be ignored.
        monkeypatch.setenv("MYAPP_", "ignored")

        cfg = load_config("myapp", yaml_path)

        assert cfg.discord.mode == "webhook"

    def test_env_can_add_new_keys_not_in_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\n",
        )
        monkeypatch.setenv("MYAPP_TIMEZONE", "Asia/Tokyo")

        cfg = load_config("myapp", yaml_path)

        assert cfg.timezone == "Asia/Tokyo"

    def test_dotenv_file_is_loaded_into_environ(
        self, tmp_path: Path
    ) -> None:
        """An ``env_path`` file should be parsed by python-dotenv and its keys
        should overlay onto the merged dict (matching the prefix convention).
        """
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\nlogging:\n  level: INFO\n",
        )
        env_path = _write_env(
            tmp_path / ".env",
            "MYAPP_LOGGING__LEVEL=ERROR\n",
        )

        # Ensure the host shell has not already set this var.
        os.environ.pop("MYAPP_LOGGING__LEVEL", None)

        cfg = load_config("myapp", yaml_path, env_path=env_path)

        assert cfg.logging.level == "ERROR"

    def test_existing_os_environ_wins_over_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """python-dotenv does not overwrite existing os.environ values by default;
        the loader should honour that precedence.
        """
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\nlogging:\n  level: INFO\n",
        )
        env_path = _write_env(
            tmp_path / ".env",
            "MYAPP_LOGGING__LEVEL=ERROR\n",
        )
        monkeypatch.setenv("MYAPP_LOGGING__LEVEL", "CRITICAL")

        cfg = load_config("myapp", yaml_path, env_path=env_path)

        assert cfg.logging.level == "CRITICAL"


# ---------------------------------------------------------------------------
# ConfigError surface area
# ---------------------------------------------------------------------------


class TestConfigError:
    """All failure modes must be raised as ``ConfigError``."""

    def test_missing_yaml_raises_config_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.yaml"
        with pytest.raises(ConfigError, match="not found"):
            load_config("myapp", missing)

    def test_invalid_yaml_syntax_raises_config_error(
        self, tmp_path: Path
    ) -> None:
        # Unmatched bracket + bad indent → YAML parser will reject.
        yaml_path = _write_yaml(
            tmp_path / "bad.yaml",
            "app_name: x\ndiscord:\n  mode: [oops\n",
        )
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_config("myapp", yaml_path)

    def test_pydantic_validation_failure_raises_config_error(
        self, tmp_path: Path
    ) -> None:
        # ``mode`` must be one of the literal values.
        yaml_path = _write_yaml(
            tmp_path / "bad.yaml",
            "app_name: x\ndiscord:\n  mode: invalid_value\n",
        )
        with pytest.raises(ConfigError, match="validation failed"):
            load_config("myapp", yaml_path)

    def test_missing_required_app_name_raises_config_error(
        self, tmp_path: Path
    ) -> None:
        yaml_path = _write_yaml(tmp_path / "bad.yaml", "discord:\n  mode: bot\n")
        with pytest.raises(ConfigError, match="validation failed"):
            load_config("myapp", yaml_path)

    def test_wrong_type_raises_config_error(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(
            tmp_path / "bad.yaml",
            "app_name: x\ndiscord:\n  channel_id: not-a-number\n",
        )
        with pytest.raises(ConfigError, match="validation failed"):
            load_config("myapp", yaml_path)

    def test_top_level_yaml_must_be_mapping(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(
            tmp_path / "bad.yaml",
            "- just\n- a\n- list\n",
        )
        with pytest.raises(ConfigError, match="must be a mapping"):
            load_config("myapp", yaml_path)

    def test_missing_env_file_is_not_an_error(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\n",
        )
        env_path = tmp_path / "definitely-not-here.env"

        # Must not raise — missing .env is silently skipped.
        cfg = load_config("myapp", yaml_path, env_path=env_path)
        assert cfg.app_name == "myapp"

    def test_no_env_path_argument_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: myapp\n",
        )
        cfg = load_config("myapp", yaml_path)
        assert cfg.app_name == "myapp"


# ---------------------------------------------------------------------------
# Custom config_class
# ---------------------------------------------------------------------------


class TestCustomConfigClass:
    """A subclass of ``AppConfig`` should work with ``load_config``."""

    def test_subclass_with_extra_field(self, tmp_path: Path) -> None:
        class NewsDigestConfig(AppConfig):
            feeds: list[str] = []

        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            (
                "app_name: news-digest\n"
                "feeds:\n"
                "  - https://example.com/rss\n"
                "  - https://example.org/feed\n"
            ),
        )

        cfg = load_config("news-digest", yaml_path, config_class=NewsDigestConfig)

        assert isinstance(cfg, NewsDigestConfig)
        assert cfg.feeds == [
            "https://example.com/rss",
            "https://example.org/feed",
        ]
        # Inherited fields still validate.
        assert cfg.app_name == "news-digest"
        assert cfg.discord.mode == "webhook"

    def test_subclass_validation_failure_still_raises_config_error(
        self, tmp_path: Path
    ) -> None:
        class StrictConfig(AppConfig):
            max_items: int = 5

        yaml_path = _write_yaml(
            tmp_path / "config.yaml",
            "app_name: x\nmax_items: not-an-int\n",
        )

        with pytest.raises(ConfigError, match="validation failed"):
            load_config("myapp", yaml_path, config_class=StrictConfig)


# ---------------------------------------------------------------------------
# Model smoke tests (no loader, just direct construction)
# ---------------------------------------------------------------------------


class TestModelDefaults:
    """Verify the model defaults documented in the spec."""

    def test_discord_defaults(self) -> None:
        d = DiscordConfig()
        assert d.mode == "webhook"
        assert d.webhook_url is None
        assert d.bot_token is None
        assert d.channel_id is None
        assert d.default_color == 0x5865F2

    def test_logging_defaults(self) -> None:
        l = LoggingConfig()
        assert l.level == "INFO"
        assert l.json is False
        assert l.file is None

    def test_schedule_defaults(self) -> None:
        s = ScheduleConfig()
        assert s.cron is None
        assert s.interval_seconds is None
        assert s.timezone == "UTC"

    def test_app_config_requires_app_name(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            AppConfig()  # type: ignore[call-arg]
