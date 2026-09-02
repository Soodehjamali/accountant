"""Shared configuration for all bot instances.

Reads environment variables and provides a single source of truth for
bot-related settings.  Both Telegram and Bale bots import from here.
"""

from __future__ import annotations

import os


def get_bot_api_base_url() -> str:
    """Return the backend API base URL.

    Defaults to ``http://localhost:8000`` for local development.
    """
    return os.environ.get("BOT_API_BASE_URL", "http://localhost:8000")


def get_telegram_token() -> str:
    """Return the Telegram bot token.

    Raises RuntimeError if not configured.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Configure it via environment variable or .env file."
        )
    return token


def get_bale_token() -> str:
    """Return the Bale bot token.

    Raises RuntimeError if not configured.
    """
    token = os.environ.get("BALE_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "BALE_BOT_TOKEN is not set. "
            "Configure it via environment variable or .env file."
        )
    return token


__all__ = ["get_bale_token", "get_bot_api_base_url", "get_telegram_token"]
