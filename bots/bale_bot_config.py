"""Bale-specific configuration.

Bale's API is Telegram-compatible but uses a different base URL:
    Telegram: https://api.telegram.org
    Bale:     https://tapi.bale.ai
"""

from __future__ import annotations

#: Bale Bot API base URL (without trailing slash).
BALE_API_BASE = "https://tapi.bale.ai"

__all__ = ["BALE_API_BASE"]
