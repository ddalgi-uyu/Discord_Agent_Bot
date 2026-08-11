import asyncio
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock

# Setup path for imports
os.environ["PYTHONPATH"] = "C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services"
sys.path.append("C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services")

from common.database import Database
from currency_notifier.rates import RateStore

async def init_db():
    db_path = Path("agent-services/currency_rates.db")
    print(f"Initializing database at {db_path}...")
    try:
        with Database(db_path) as db:
            store = RateStore(db)
            # RateStore constructor calls execute_write for SCHEMA and INDEX
            print("Database schema and indexes verified/created.")
    except Exception as e:
        print(f"Database init failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(init_db())
