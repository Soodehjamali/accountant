"""Request/response schemas for the bot-config admin endpoints.

Never carries a plaintext token in a response: ``token_configured`` is a
boolean and ``token_hint`` is the last 4 characters only.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class BotConfigItem(BaseModel):
    """One platform's config + live status for the admin UI."""

    platform: str
    enabled: bool
    token_configured: bool
    token_hint: str | None = None
    status: str  # NOT_CONFIGURED / DISABLED / STOPPED / RUNNING / ERROR
    last_heartbeat: datetime.datetime | None = None
    # Bot identity from getMe (display-only; never the token).
    bot_username: str | None = None
    bot_name: str | None = None


class BotConfigListResponse(BaseModel):
    items: list[BotConfigItem]


class BotConfigUpdateRequest(BaseModel):
    """Save request for one platform.

    ``token`` is optional and only replaces the stored secret when
    provided; an empty string clears the token.
    """

    enabled: bool
    token: str | None = Field(default=None, max_length=200, min_length=0)


class BotConfigUpdateResponse(BaseModel):
    ok: bool = True
    platform: str
    enabled: bool
    token_configured: bool
    token_hint: str | None = None


class BotTestConnectionResponse(BaseModel):
    ok: bool
    detail: str
    # Bot identity from getMe on success (display-only; never the token).
    bot_username: str | None = None
    bot_name: str | None = None


class BotRuntimeUpdateRequest(BaseModel):
    """Heartbeat body sent by the bot process itself (internal)."""

    status: str  # RUNNING / STOPPED / ERROR


class BotRuntimeUpdateResponse(BaseModel):
    ok: bool = True


class BotRuntimeTokenResponse(BaseModel):
    """Plaintext token for the bot process (internal, runtime-secret gated)."""

    token: str | None


__all__ = [
    "BotConfigItem",
    "BotConfigListResponse",
    "BotConfigUpdateRequest",
    "BotConfigUpdateResponse",
    "BotRuntimeTokenResponse",
    "BotRuntimeUpdateRequest",
    "BotRuntimeUpdateResponse",
    "BotTestConnectionResponse",
]