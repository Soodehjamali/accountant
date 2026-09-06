import asyncio
from bots.shared import get_api_client, api_verify_phone, api_get_products, store_token

async def main():
    # replace with the same phone you just verified successfully with
    result = await api_verify_phone(
        phone_number="+989131917993",
        platform="telegram",
        chat_id="debug-script",
    )
    print("REP ID:", result.get("representative_id"))
    token = result["access_token"]
    rep_id = result["representative_id"]

    client = await get_api_client()
    resp = await client.get(
        f"/bot/reps/{rep_id}/products",
        headers={"Authorization": f"Bearer {token}"},
    )
    print("FULL URL:", resp.request.url)
    print("STATUS:", resp.status_code)
    print("BODY:", resp.text)

asyncio.run(main())
