"""FastAPI dependency for bot-authenticated endpoints.

``get_bot_representative`` validates the JWT token from the ``Authorization``
header (issued by ``bot_phone_service.verify_phone``) and returns the
``Representative`` identified by the token's ``sub`` claim.

Session enforcement (ADR-013):
    Tokens issued by ``bot_phone_service.verify_phone`` carry a
    ``session_id`` claim pointing at the persistent ``bot_session`` row.
    On every request this dependency re-checks that row: a ``REVOKED``
    session, a session whose ``status`` is ``EXPIRED``, or a session whose
    ``expires_at`` has passed rejects the request immediately -- the bot
    can't keep using a revoked binding until the short-lived JWT happens to
    expire.  ``last_seen`` is refreshed on each authenticated request.

    Legacy bot tokens without a ``session_id`` claim (issued before the
    session-binding flow) are still accepted for the representative/active
    checks only -- they cannot be revoked at session granularity.

RBAC:
    ``require_bot_permission(permission_code)`` resolves the ``AppUser``
    linked to the representative (via ``representative_id`` FK) and checks
    ``rbac_service.user_has_permission`` -- the same RBAC model the old
    command-service architecture enforced.  The bot is not an unrestricted
    backdoor: read endpoints require ``BOT_QUERY``, write endpoints require
    ``BOT_WRITE``.

Usage::

    @router.get("/bot/reps/{rep_id}/inventory")
    def get_inventory(
        rep_id: uuid.UUID,
        rep: Representative = Depends(require_bot_rep_scope),
        db: Session = Depends(get_db),
    ) -> ...:
        # Use rep.id (from token), NOT rep_id (from URL).
        ...
"""

from __future__ import annotations

import datetime
import uuid
from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.db import get_db
from database.models.bot_session import BotSession
from database.models.representative import Representative
from security import InvalidTokenError, decode_access_token
from services import rbac_service

#: Bot bearer scheme -- same header format as app auth but different token.
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_bot_token(token: str, secret_key: str) -> tuple[uuid.UUID, str | None]:
    """Decode a bot JWT and return ``(representative_id, session_id)``.

    ``session_id`` is ``None`` for legacy tokens without the claim.
    """
    try:
        payload = decode_access_token(token, secret_key=secret_key)
    except InvalidTokenError as exc:
        raise _unauthorized(str(exc)) from exc

    try:
        rep_id = uuid.UUID(payload.subject)
    except ValueError as exc:
        raise _unauthorized("Token subject is not a valid representative id.") from exc

    session_id: str | None = payload.extra_claims.get("session_id")
    return rep_id, session_id


def _load_valid_session(db: Session, session_id: str) -> BotSession:
    """Load a bot session and reject revoked/expired ones.

    Raises:
        HTTPException(401): session missing, REVOKED, EXPIRED, or past expiry.
    """
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError as exc:
        raise _unauthorized("Token session id is not a valid UUID.") from exc

    bot_session = db.get(BotSession, session_uuid)
    if bot_session is None:
        raise _unauthorized("Bot session no longer exists.")

    now = datetime.datetime.now(datetime.timezone.utc)
    if bot_session.status == "REVOKED":
        raise _unauthorized("Bot session has been revoked.")
    if bot_session.status == "EXPIRED":
        raise _unauthorized("Bot session has expired.")
    if bot_session.expires_at is not None and bot_session.expires_at <= now:
        raise _unauthorized("Bot session has expired.")

    # Refresh last_seen on every authenticated request.
    bot_session.last_seen = now
    db.flush()
    return bot_session


def get_bot_representative(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Representative:
    """Return the ``Representative`` identified by a valid bot JWT token.

    The token is issued by ``bot_phone_service.verify_phone`` and contains:
    - ``sub``: representative_id (UUID string)
    - ``type``: "bot" (distinguishes from app auth tokens)
    - ``session_id``: the persistent ``bot_session`` id (when issued by the
      current flow)
    - ``exp``: expiry time

    This dependency does NOT enforce ``rep_id`` matching -- that is each
    endpoint's responsibility (see module docstring usage example).

    Raises:
        HTTPException(401): missing/malformed/expired token, revoked/expired
          session, or the representative no longer exists or is not ACTIVE.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated.")

    settings = get_settings()
    rep_id, session_id = _decode_bot_token(
        credentials.credentials, secret_key=settings.secret_key
    )

    # Session-level revocation/expiry check (only possible when the token
    # carries a session_id claim -- i.e. the ADR-013 flow).
    if session_id is not None:
        _load_valid_session(db, session_id)

    rep = db.get(Representative, rep_id)
    if rep is None or rep.deleted_at is not None:
        raise _unauthorized("Representative no longer exists.")
    if rep.status != "ACTIVE":
        raise _unauthorized("Representative account is not active.")

    return rep


def require_bot_rep_scope(
    rep_id: uuid.UUID,
    rep: Representative = Depends(get_bot_representative),
) -> Representative:
    """Dependency that enforces the URL ``rep_id`` matches the token's representative.

    This is a convenience wrapper that combines token validation (via
    ``get_bot_representative``) with URL-parameter scope enforcement.

    Usage::

        @router.get("/bot/reps/{rep_id}/inventory")
        def get_inventory(
            rep: Representative = Depends(require_bot_rep_scope),
            ...
        ):
            # rep.id is guaranteed to match the URL's rep_id.
            ...

    The URL parameter ``rep_id`` is extracted by FastAPI's path parsing
    and passed to this dependency for comparison.  If the token's
    ``representative_id`` does not match, a 403 is raised.
    """
    if rep.id != rep_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: token does not match the requested representative.",
        )
    return rep


def require_bot_permission(permission_code: str) -> Callable[..., Representative]:
    """Return a FastAPI dependency that requires the bot's representative to
    hold ``permission_code`` (via their linked ``AppUser``'s roles).

    The bot identity is a ``Representative``; RBAC permissions live on
    ``AppUser`` roles, so the dependency resolves the ``AppUser`` linked to
    the representative (``AppUser.representative_id`` FK -- never
    ``AppUser.id``) and checks ``rbac_service.user_has_permission``.

    A representative with no linked ``AppUser`` can hold no permissions and
    is denied unconditionally -- the bot can never bypass RBAC.

    Raises:
        HTTPException(403): representative's linked user lacks the permission.
    """
    from services import bot_command_service

    def _dependency(
        rep: Representative = Depends(get_bot_representative),
        db: Session = Depends(get_db),
    ) -> Representative:
        app_user = bot_command_service._find_user_by_representative(db, rep.id)
        if app_user is None or not rbac_service.user_has_permission(
            db, app_user.id, permission_code
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission '{permission_code}'.",
            )
        return rep

    return _dependency


__all__ = [
    "get_bot_representative",
    "require_bot_permission",
    "require_bot_rep_scope",
]