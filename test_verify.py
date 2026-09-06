import asyncio
from bots.shared import get_api_client

async def main():
    client = await get_api_client()
    print("BASE URL:", client.base_url)
    resp = await client.post(
        "/bot/verify-phone",
        json={"phone_number": "+989120000000", "platform": "TELEGRAM", "chat_id": "999"},
    )
    print("STATUS:", resp.status_code)
    print("BODY:", resp.text)

asyncio.run(main())
