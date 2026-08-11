import asyncio
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta

# Setup path for imports
os.environ["PYTHONPATH"] = "C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services"
sys.path.append("C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services")

from common.config_loader import load_config, AppConfig
from common.database import Database
from currency_notifier.pipeline import run
from currency_notifier.config import CurrencyNotifierConfig
from currency_notifier.rates import RateStore, fetch_rate

async def test_visual_alerts():
    app_name = "currency-notifier"
    yaml_path = Path("agent-services/config/currency_notifier.yaml")
    db_path = Path("agent-services/currency_rates.db")
    
    print("Testing Tiered Alerts and Visualization...")
    
    try:
        config = load_config(app_name, yaml_path, config_class=CurrencyNotifierConfig)
        
        mock_notifier = AsyncMock()
        mock_notifier.send_embed.return_value = True
        mock_notifier.close = AsyncMock()
        
        with Database(db_path) as db:
            # 1. Clear and Seed the database
            store = RateStore(db)
            db.execute_write("DELETE FROM currency_rates")
            
            pair = "USD/JPY"
            now = datetime.now(timezone.utc)
            for i in range(30, 0, -1):
                ts = now - timedelta(days=i)
                rate = 160.0 - (i * 0.1) 
                db.execute_write(
                    "INSERT INTO currency_rates (pair, rate, timestamp) VALUES (?, ?, ?)",
                    (pair, rate, ts.isoformat(timespec="seconds").replace("+00:00", "Z"))
                )
            
            # 2. Monkeypatch fetch_rate to return a value that triggers the alert
            # and bypasses the 'should_notify' dedup logic (which compares with last stored)
            original_fetch = RateStore.should_notify # This is actually a method on RateStore
            # Let's just mock the fetch_rate function in the rates module
            import currency_notifier.rates as rates_mod
            original_fetch_func = rates_mod.fetch_rate
            rates_mod.fetch_rate = AsyncMock(return_value=140.0) # Definitely a la la a strong buy
            
            # Also force the config thresholds
            config.pairs[0].base = "USD"
            config.pairs[0].quote = "JPY"
            config.pairs[0].static_threshold = 200.0 
            
            # 3. Trigger the pipeline
            delivered = await run(config, db, notifier=mock_notifier)
            
            # Restore
            rates_mod.fetch_rate = original_fetch_func

            if mock_notifier.send_embed.called:
                args, kwargs = mock_notifier.send_embed.call_args
                last_call = args[0] if args else kwargs.get('embed')
                
                print(f"Pipeline executed. Delivered: {delivered}")
                print(f"Embed Title: {last_call.title.encode('ascii', 'ignore').decode()}")
                print(f"Embed Color: {hex(last_call.color)}")
                print(f"Chart Image Path: {last_call.image}")
                
                if last_call.image and Path(last_call.image).exists():
                    print("Chart image was successfully generated on disk.")
                else:
                    print("No chart image generated or file not found.")
            else:
                print("No alerts were delivered even with forced thresholds.")
                
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_visual_alerts())
