"""Bot configuration endpoints: ``/api/v1/bot-config``.

Admin surface (all gated behind ``BOT_MANAGE``):
    * ``GET /bot-config`` -- per-platform config + live status.
    * ``PUT /bot-config/{platform}`` -- save enabled flag and/or token.
    * ``POST /bot-config/{platform}/test`` -- test the stored token against
      the platform's ``getMe`` API.

Bot-process surface (gated by the shared ``X-Bot-Runtime-Secret`` header,
not a user login -- the bot processes have no human identity):
    * ``POST /bot-config/{platform}/runtime`` -- heartbeat (RUNNING/STOPPED/ERROR).
    * ``GET /bot-config/{platform}/token`` -- plaintext token at startup.

Secrets: the admin endpoints never return a plaintext token.  Only the
last-4-char hint and a ``token_configured`` boolean are exposed.  Tokens
are stored Fernet-encrypted at rest and never sent to the frontend.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.bot_config import (
    BotConfigItem,
    BotConfigListResponse,
    BotConfigUpdateRequest,
    BotConfigUpdateResponse,
    BotRuntimeTokenResponse,
    BotRuntimeUpdateRequest,
    BotRuntimeUpdateResponse,
    BotTestConnectionResponse,
)
from database.models.app_user import AppUser
from services import bot_config_service

router = APIRouter(prefix="/bot-config", tags=["bot-config"])

BOT_MANAGE_PERMISSION_CODE = "BOT_MANAGE"
_require_bot_manage = require_permission(BOT_MANAGE_PERMISSION_CODE)

SUPPORTED_PLATFORMS = ("telegram", "bale")

#: Runtime secret the bot processes send as ``X-Bot-Runtime-Secret``.
#: Default is dev-only; production must set ``BOT_RUNTIME_SECRET``.
_DEV_RUNTIME_SECRET = "dev-bot-runtime-secret"


def _runtime_secret() -> str:
    return os.environ.get("BOT_RUNTIME_SECRET", _DEV_RUNTIME_SECRET)


def _require_runtime_secret(
    x_bot_runtime_secret: str | None = Header(default=None),
) -> None:
    """Internal gate for bot-process endpoints."""
    if x_bot_runtime_secret != _runtime_secret():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bot runtime secret.",
        )


def _validate_platform(platform: str) -> str:
    code = platform.upper()
    if code not in bot_config_service.SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unsupported platform '{platform}'.",
        )
    return code


# ---------------------------------------------------------------------------
# Admin surface
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=BotConfigListResponse,
    summary="List bot platform configs and live status",
)
def list_bot_configs(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_bot_manage),
) -> BotConfigListResponse:
    items: list[BotConfigItem] = []
    for code in ("TELEGRAM", "BALE"):
        config = bot_config_service.get_config(db, code)
        token_configured = config is not None and config.token_ciphertext is not None
        items.append(
            BotConfigItem(
                platform=code,
                enabled=bool(config.enabled) if config else False,
                token_configured=token_configured,
                token_hint=config.token_hint if config else None,
                status=bot_config_service.get_status(db, code),
                last_heartbeat=config.last_heartbeat if config else None,
                bot_username=config.bot_username if config else None,
                bot_name=config.bot_name if config else None,
            )
        )
    db.flush()
    db.commit()
    return BotConfigListResponse(items=items)


@router.put(
    "/{platform}",
    response_model=BotConfigUpdateResponse,
    summary="Save bot config for a platform (enabled and/or token)",
)
def update_bot_config(
    platform: str,
    body: BotConfigUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_bot_manage),
) -> BotConfigUpdateResponse:
    code = _validate_platform(platform)
    settings = get_settings()
    config = bot_config_service.save_config(
        db,
        code,
        enabled=body.enabled,
        token=body.token,
        updated_by=current_user.id,
        secret_key=settings.secret_key,
    )
    db.commit()
    return BotConfigUpdateResponse(
        platform=code,
        enabled=config.enabled,
        token_configured=config.token_ciphertext is not None,
        token_hint=config.token_hint,
    )


@router.post(
    "/{platform}/test",
    response_model=BotTestConnectionResponse,
    summary="Test the stored bot token against the platform API",
)
def test_bot_connection(
    platform: str,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_bot_manage),
) -> BotTestConnectionResponse:
    code = _validate_platform(platform)
    settings = get_settings()
    token = bot_config_service.get_plain_token(db, code, secret_key=settings.secret_key)
    if token is None:
        return BotTestConnectionResponse(ok=False, detail="No token configured.")
    ok, detail, identity = bot_config_service.test_connection(code, token)
    if ok and identity is not None:
        # Persist the real getMe identity so the UI shows the bot's actual
        # name/username without the admin typing them (never the token).
        bot_config_service.set_identity(
            db,
            code,
            bot_id=identity["bot_id"],
            username=identity["username"],
            name=identity["name"],
            updated_by=_current_user.id,
        )
        db.commit()
        return BotTestConnectionResponse(
            ok=True,
            detail=detail,
            bot_username=identity["username"],
            bot_name=identity["name"],
        )
    return BotTestConnectionResponse(ok=ok, detail=detail)


# ---------------------------------------------------------------------------
# Bot-process surface (runtime secret gated)
# ---------------------------------------------------------------------------


@router.post(
    "/{platform}/runtime",
    response_model=BotRuntimeUpdateResponse,
    summary="Report bot process runtime status (heartbeat)",
    dependencies=[Depends(_require_runtime_secret)],
)
def update_runtime_status(
    platform: str,
    body: BotRuntimeUpdateRequest,
    db: Session = Depends(get_db),
) -> BotRuntimeUpdateResponse:
    code = _validate_platform(platform)
    settings = get_settings()
    try:
        bot_config_service.set_runtime_status(
            db,
            code,
            status=body.status,
            updated_by=_system_user_id(db),
            secret_key=settings.secret_key,
        )
    except bot_config_service.InvalidRuntimeStatusError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return BotRuntimeUpdateResponse(ok=True)


@router.get(
    "/{platform}/token",
    response_model=BotRuntimeTokenResponse,
    summary="Fetch the plaintext bot token for a bot process (startup)",
    dependencies=[Depends(_require_runtime_secret)],
)
def get_runtime_token(
    platform: str,
    db: Session = Depends(get_db),
) -> BotRuntimeTokenResponse:
    code = _validate_platform(platform)
    settings = get_settings()
    token = bot_config_service.get_plain_token(
        db, code, secret_key=settings.secret_key, require_enabled=True
    )
    return BotRuntimeTokenResponse(token=token)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _system_user_id(db: Session) -> uuid.UUID:
    from sqlalchemy import select

    from database.models.app_user import AppUser
    from services.bootstrap_service import SYSTEM_USERNAME

    user = db.execute(
        select(AppUser.id).where(AppUser.username == SYSTEM_USERNAME)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System user not found.",
        )
    return user


__all__ = ["router"]