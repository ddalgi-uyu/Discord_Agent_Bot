import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Setup path for imports
import os
os.environ["PYTHONPATH"] = "C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services"
sys.path.append("C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services")

from common.config_loader import load_config, AppConfig
from common.database import Database
from currency_notifier.pipeline import run
from currency_notifier.config import CurrencyNotifierConfig

async def test_currency_pipeline():
    print("Starting Currency Notifier Pipeline Test...")
    
    # 1. Load config
    app_name = "currency-notifier"
    yaml_path = Path("currency_test_config.yaml")
    config = load_config(app_name, yaml_path, config_class=CurrencyNotifierConfig)
    print(f"Config loaded for {app_name}")

    # 2. Mock Database (to avoid actual SQLite file creation/locking in test)
    mock_db = MagicMock(spec=Database)
    
    # 3. Mock Notifier to avoid 405 errors and actual web calls
    mock_notifier = AsyncMock()
    mock_notifier.send_embed.return_value = True
    mock_notifier.close = AsyncMock()

    # 4. Run pipeline with mocks
    print("Running pipeline cycle...")
    try:
        # We inject the mock notifier to test the internal logic without hitting Discord
        delivered = await run(config, mock_db, notifier=mock_notifier)
        print(f"Pipeline cycle completed. Delivered alerts: {delivered}")
    except Exception as e:
        print(f"Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_currency_pipeline())
