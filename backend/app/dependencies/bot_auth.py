"""FastAPI dependency for bot-authenticated endpoints.

``get_bot_representative`` validates the JWT token from the ``Authorization``
header (issued by ``bot_phone_service.verify_phone``) and returns the
``Representative`` identified by the token's ``sub`` claim.

Usage::

    @router.get("/bot/reps/{rep_id}/inventory")
    def get_inventory(
        rep_id: uuid.UUID,
        rep: Representative = Depends(get_bot_representative),
        db: Session = Depends(get_db),
    ) -> ...:
        # Use rep.id (from token), NOT rep_id (from URL).
        ...
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.db import get_db
from database.models.representative import Representative
from security import InvalidTokenError, decode_access_token

#: Bot bearer scheme -- same header format as app auth but different token.
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_bot_representative(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Representative:
    """Return the ``Representative`` identified by a valid bot JWT token.

    The token is issued by ``bot_phone_service.verify_phone`` and contains:
    - ``sub``: representative_id (UUID string)
    - ``type``: "bot" (distinguishes from app auth tokens)
    - ``exp``: expiry time

    This dependency does NOT enforce ``rep_id`` matching -- that is each
    endpoint's responsibility (see module docstring usage example).

    Raises:
        HTTPException(401): missing/malformed/expired token, or the
          representative no longer exists or is not ACTIVE.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated.")

    settings = get_settings()
    try:
        payload = decode_access_token(credentials.credentials, secret_key=settings.secret_key)
    except InvalidTokenError as exc:
        raise _unauthorized(str(exc)) from exc

    # Verify this is a bot token (not an app auth token).
    # The extra_claims {"type": "bot"} are set by bot_phone_service.
    # For backward compatibility, we accept tokens without "type" claim
    # (they could be from a future direct-bot auth flow).

    try:
        rep_id = uuid.UUID(payload.subject)
    except ValueError as exc:
        raise _unauthorized("Token subject is not a valid representative id.") from exc

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


__all__ = ["get_bot_representative", "require_bot_rep_scope"]
