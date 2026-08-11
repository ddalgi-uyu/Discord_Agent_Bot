import asyncio
import httpx

async def main():
    url = "https://api.frankfurter.app/latest?from=USD&to=JPY"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers) as client:
        print(f"Requesting {url}...")
        try:
            resp = await client.get(url)
            print(f"Status: {resp.status_code}")
            print(f"Content-Type: {resp.headers.get('Content-Type')}")
            print(f"Body: {resp.text[:200]}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
