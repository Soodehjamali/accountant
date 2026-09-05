"""Shared configuration for all bot instances.

Reads environment variables and provides a single source of truth for
bot-related settings.  Both Telegram and Bale bots import from here.

Token resolution priority:
    1. Backend-managed config (``GET /api/v1/bot-config/{platform}/token``
       with the runtime secret) -- the ERP admin UI stores and encrypts the
       token, so no env var is needed on the machine running the bot.
    2. Environment variables (``TELEGRAM_BOT_TOKEN`` / ``BALE_BOT_TOKEN``)
       as a development fallback.

Telegram network routing:
    ``TELEGRAM_PROXY`` is an optional local HTTP(S)/SOCKS proxy URL consumed
    by the aiogram Telegram entry point.  It is loaded from the process
    environment (including the project-root ``.env``) and does not affect the
    backend or Bale clients.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


_TELEGRAM_PROXY_SCHEMES = frozenset({"http", "socks4", "socks4a", "socks5", "socks5h"})


def get_bot_api_base_url() -> str:
    """Return the backend API base URL.

    Defaults to ``http://localhost:8000`` for local development.
    """
    return os.environ.get("BOT_API_BASE_URL", "http://localhost:8000")


def get_telegram_proxy() -> str | None:
    """Return the optional proxy URL for aiogram's Telegram session.

    The URL must point to a proxy listener provided by the local network
    setup (for example, a Sing-box HTTP or SOCKS inbound).  No proxy is used
    when the variable is empty.  Validation here fails early with a useful
    error instead of allowing an invalid scheme to fail during ``getMe``.
    """
    value = os.environ.get("TELEGRAM_PROXY", "").strip()
    if not value:
        return None

    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(
            "TELEGRAM_PROXY must include a valid proxy host and port."
        ) from exc

    if parsed.scheme.lower() not in _TELEGRAM_PROXY_SCHEMES:
        supported = ", ".join(sorted(_TELEGRAM_PROXY_SCHEMES))
        raise RuntimeError(
            f"TELEGRAM_PROXY must use one of: {supported}."
        )
    if not parsed.hostname or port is None:
        raise RuntimeError(
            "TELEGRAM_PROXY must include a proxy host and port."
        )
    return value


def get_bot_runtime_secret() -> str:
    """Return the runtime secret used to talk to the bot-config endpoints.

    Must match the backend's ``BOT_RUNTIME_SECRET`` (or the backend's dev
    default when unset).
    """
    return os.environ.get("BOT_RUNTIME_SECRET", "dev-bot-runtime-secret")


#: Env-var fallback per platform (development only -- the ERP admin UI is
#: the primary configuration path; see module docstring for the precedence).
_TOKEN_ENV_VARS = {
    "telegram": "TELEGRAM_BOT_TOKEN",
    "bale": "BALE_BOT_TOKEN",
}


def resolve_platform_token(platform: str) -> str | None:
    """Return the effective token for ``platform``, or ``None`` when unset.

    Precedence (single source of truth for the whole bot runtime):
        1. Backend-managed config (admin UI -> encrypted ``bot_config`` row).
        2. Environment variable (``TELEGRAM_BOT_TOKEN`` / ``BALE_BOT_TOKEN``)
           as a development fallback.

    Never logs the token.
    """
    token = get_backend_token(platform)
    if not token:
        token = os.environ.get(_TOKEN_ENV_VARS.get(platform, ""), "") or None
    return token


def get_telegram_token() -> str:
    """Return the Telegram bot token (backend config first, then env).

    Raises RuntimeError if neither source has a token.
    """
    token = resolve_platform_token("telegram")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Configure it via the ERP "
            "admin (Settings -> Bots) or the TELEGRAM_BOT_TOKEN "
            "environment variable."
        )
    return token


def get_bale_token() -> str:
    """Return the Bale bot token (backend config first, then env).

    Raises RuntimeError if neither source has a token.
    """
    token = resolve_platform_token("bale")
    if not token:
        raise RuntimeError(
            "BALE_BOT_TOKEN is not set. Configure it via the ERP "
            "admin (Settings -> Bots) or the BALE_BOT_TOKEN "
            "environment variable."
        )
    return token


def get_backend_token(platform: str) -> str | None:
    """Fetch the plaintext token from the backend's bot-config endpoint.

    Returns ``None`` when the backend is unreachable, the platform is
    disabled, or no token is stored -- the env-var fallback then applies.

    Never logs the token.
    """
    import httpx

    base = get_bot_api_base_url()
    try:
        response = httpx.get(
            f"{base}/api/v1/bot-config/{platform}/token",
            headers={"X-Bot-Runtime-Secret": get_bot_runtime_secret()},
            timeout=10.0,
        )
        if response.status_code != 200:
            return None
        body = response.json()
        token = body.get("token")
        return token if token else None
    except Exception:  # noqa: BLE001 - backend may be down at boot
        return None


__all__ = [
    "get_backend_token",
    "get_bale_token",
    "get_bot_api_base_url",
    "get_bot_runtime_secret",
    "get_telegram_proxy",
    "get_telegram_token",
    "resolve_platform_token",
]