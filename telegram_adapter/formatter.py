"""Telegram response formatting.

Converts platform-agnostic :class:`BotResponse` instances into the
parameter dicts expected by Telegram's ``sendMessage`` API endpoint.

**Zero business logic.**  This module only maps response fields to
Telegram API parameters.
"""

from __future__ import annotations

from typing import Any

from services.bot_command_service import BotResponse


def format_response(
    response: BotResponse,
    *,
    chat_id: str,
) -> dict[str, Any]:
    """Convert a ``BotResponse`` into Telegram ``sendMessage`` parameters.

    Returns a dict suitable for passing to:
        ``POST https://api.telegram.org/bot<token>/sendMessage``

    The dict contains:
        - ``chat_id``: target chat.
        - ``text``: message text.
        - ``parse_mode``: optional (Markdown, HTML, etc.).
        - ``reply_to_message_id``: optional, for threading.
    """
    params: dict[str, Any] = {
        "chat_id": chat_id,
        "text": response.text,
    }
    if response.parse_mode is not None:
        params["parse_mode"] = response.parse_mode
    if response.reply_to_message_id is not None:
        params["reply_to_message_id"] = response.reply_to_message_id
    return params


__all__ = ["format_response"]
