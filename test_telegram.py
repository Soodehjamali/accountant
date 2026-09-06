import asyncio
import aiohttp

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.telegram.org", timeout=aiohttp.ClientTimeout(total=10)) as resp:
            print(resp.status)

asyncio.run(main())
