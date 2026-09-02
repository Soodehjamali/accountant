"""Bot project — shared Telegram + Bale adapters.

This package contains the platform-agnostic bot logic and platform-specific
entry points for Telegram and Bale messaging platforms.

Usage::

    # Telegram bot
    python -m bots.telegram_bot

    # Bale bot
    python -m bots.bale_bot

Environment variables:
    TELEGRAM_BOT_TOKEN — Telegram bot token from @BotFather
    BALE_BOT_TOKEN — Bale bot token
    BOT_API_BASE_URL — Backend API base URL (default: http://localhost:8000)
"""

__all__ = []
