"""Telegram message normalization.

Converts Telegram ``Update`` objects (received as JSON dicts from the
Telegram Bot API) into platform-agnostic :class:`BotMessage` instances
that ``services/bot_command_service.py`` can process.

**Zero business logic.**  This module only extracts fields from the
Telegram Update structure and maps them onto ``BotMessage``.
"""

from __future__ import annotations

from typing import Any

from services.bot_command_service import BotMessage


def normalize_update(update: dict[str, Any]) -> BotMessage | None:
    """Convert a Telegram ``Update`` dict into a ``BotMessage``.

    Returns ``None`` if the update is not a message update (e.g. a
    callback query, inline query, or other non-message update type).

    The Telegram Bot API sends updates in this structure::

        {
            "update_id": 123,
            "message": {
                "message_id": 456,
                "from": {"id": 789, "first_name": "...", "username": "..."},
                "chat": {"id": 789, "type": "private"},
                "date": 1234567890,
                "text": "/orders"
            }
        }

    We extract:
        - ``from.id`` → ``platform_user_id`` (as string)
        - ``message.text`` → ``text``
        - ``message.message_id`` → ``metadata["telegram_message_id"]``
        - ``update_id`` → ``metadata["telegram_update_id"]``
    """
    message_data = update.get("message")
    if message_data is None:
        return None

    text = message_data.get("text")
    if text is None:
        return None

    sender = message_data.get("from", {})
    platform_user_id = str(sender.get("id", ""))

    if not platform_user_id:
        return None

    metadata: dict[str, Any] = {
        "telegram_message_id": str(message_data.get("message_id", "")),
        "telegram_update_id": str(update.get("update_id", "")),
        "telegram_chat_id": str(message_data.get("chat", {}).get("id", "")),
    }

    # Add sender metadata for logging (no security reliance -- see
    # bot_command_service.py).
    if sender.get("first_name"):
        metadata["sender_first_name"] = sender["first_name"]
    if sender.get("username"):
        metadata["sender_username"] = sender["username"]

    return BotMessage(
        platform_user_id=platform_user_id,
        platform_code="TELEGRAM",
        text=text,
        metadata=metadata,
    )


__all__ = ["normalize_update"]
