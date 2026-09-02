"""Bale bot entry point using aiogram 3.

Run with::

    python -m bots.bale_bot

Or::

    BALE_BOT_TOKEN=your_token python -m bots.bale_bot

Bale (بله) is an Iranian messaging platform with a Telegram-compatible API.
The only difference from Telegram is the base URL:
    Telegram: https://api.telegram.org/bot<TOKEN>/
    Bale:     https://tapi.bale.ai/bot<TOKEN>/

This module reuses ALL handlers from ``bots.telegram_bot`` — the only
change is the bot token source and the API session configuration.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from bots.bale_bot_config import BALE_API_BASE
from bots.config import get_bale_token
from bots.telegram_bot import router  # Reuse all Telegram handlers
from bots.shared import close_api_client

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the Bale bot."""
    token = get_bale_token()

    # Create a session with Bale's API base URL instead of Telegram's.
    session = AiohttpSession(api_base=BALE_API_BASE)
    bot = Bot(token=token, session=session)

    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bale bot starting...")

    try:
        await dp.start_polling(bot)
    finally:
        await close_api_client()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
