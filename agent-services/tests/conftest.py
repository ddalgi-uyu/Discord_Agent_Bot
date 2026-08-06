"""Shared test fixtures for agent-services test suite."""
import os
from pathlib import Path

import pytest

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture
def project_root() -> Path:
    return ROOT


@pytest.fixture
def config_dir(project_root: Path) -> Path:
    return project_root / "config"


@pytest.fixture
def mock_discord_webhook_url() -> str:
    return "https://discord.com/api/webhooks/123456/test-token"


@pytest.fixture
def mock_discord_bot_token() -> str:
    return "test-bot-token-1234567890"


@pytest.fixture
def mock_discord_channel_id() -> int:
    return 123456789012345678
