import asyncio
from common.database import Database
from currency_notifier.rates import RateStore

async def main():
    db = Database('agent-services/currency_rates.db')
    store = RateStore(db)
    rate = await store.get_last_rate('USD/JPY')
    print(f"Last rate for USD/JPY: {rate}")
    db.close()

if __name__ == "__main__":
    asyncio.run(main())
