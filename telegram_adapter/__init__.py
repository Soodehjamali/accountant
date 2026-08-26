"""Telegram bot adapter (Platform A -- SRS S5).

This package is the **only layer** that knows about Telegram's API.
It handles:
    - Telegram-specific I/O (polling / webhooks -- Phase A uses polling).
    - Message normalization: ``telegram.types.Update`` → ``BotMessage``.
    - Response formatting: ``BotResponse`` → Telegram ``sendMessage``.
    - Telegram bot lifecycle (start/stop polling).

**Zero business logic.**  This package imports nothing from
``services/bot_command_service.py`` directly -- it receives a
``process_message`` callback via dependency injection.

Architecture:
    ``TelegramBot`` (``adapter.py``) -- owns the polling loop and
    Telegram API client.

    ``normalize_update()`` (``normalizer.py``) -- converts a Telegram
    ``Update`` into a ``BotMessage``.

    ``format_response()`` (``formatter.py``) -- converts a ``BotResponse``
    into Telegram API parameters.

All Telegram API calls go through ``httpx`` (an async-capable HTTP client
already common in the Python ecosystem).  No Telegram-specific SDK
dependency is required -- the HTTP API is simple enough to call directly.
"""

from telegram_adapter.adapter import TelegramBot
from telegram_adapter.formatter import format_response
from telegram_adapter.normalizer import normalize_update

__all__ = ["TelegramBot", "format_response", "normalize_update"]
