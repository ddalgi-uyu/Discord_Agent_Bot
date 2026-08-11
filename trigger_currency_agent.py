import asyncio
import sys
import os
from pathlib import Path

# Setup path for imports
os.environ["PYTHONPATH"] = "C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services"
sys.path.append("C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services")

from common.config_loader import load_config, AppConfig
from common.database import Database
from currency_notifier.pipeline import run
from currency_notifier.config import CurrencyNotifierConfig

async def main():
    app_name = "currency-notifier"
    yaml_path = Path("agent-services/config/currency_notifier.yaml")
    db_path = Path("agent-services/currency_rates.db")
    
    print(f"Starting Manual Pipeline Trigger for {app_name}...")
    
    try:
        # 1. Load the actual production config
        config = load_config(app_name, yaml_path, config_class=CurrencyNotifierConfig)
        print(f"Config loaded. Monitoring: {[p.base + '/' + p.quote for p in config.pairs]}")
        
        # 2. Connect to the actual DB
        with Database(db_path) as db:
            # 3. Execute the pipeline
            # We let it create its own client and notifier using the config
            delivered = await run(config, db)
            print(f"Cycle completed. Alerts delivered to Discord: {delivered}")
            
    except Exception as e:
        print(f"Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
