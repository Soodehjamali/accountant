"""FastAPI dependency providing the authenticated caller.

``get_current_user`` is what a protected endpoint depends on, e.g.::

    @router.get("/orders")
    def list_orders(
        current_user: AppUser = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> ...:
        ...

It does three things, in order: (1) extract the Bearer token from the
``Authorization`` header, (2) verify its signature/expiry via
``security.decode_access_token``, (3) load the ``AppUser`` its ``sub``
claim points at and confirm the account is still usable (``ACTIVE``, not
soft-deleted) -- a token issued before an account was deactivated must
stop working immediately, not just at its natural expiry.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.db import get_db
from database.models.app_user import AppUser
from security import InvalidTokenError, decode_access_token

#: ``auto_error=True`` (the default) -- FastAPI returns 403 itself when the
#: header is missing entirely, before this dependency's own body runs;
#: this dependency additionally normalizes that to 401 (see below), since
#: "no credentials" and "bad credentials" should look identical to a
#: client (401 + WWW-Authenticate), not split across two status codes.
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AppUser:
    """Return the ``AppUser`` identified by a valid Bearer access token.

    Raises:
        HTTPException(401): missing/malformed/expired/tampered token, or
          the token is well-formed but no longer maps to a usable account
          (deleted, deactivated, or the account itself was deleted after
          the token was issued).
    """

    if credentials is None:
        raise _unauthorized("Not authenticated.")

    settings = get_settings()
    try:
        payload = decode_access_token(credentials.credentials, secret_key=settings.secret_key)
    except InvalidTokenError as exc:
        raise _unauthorized(str(exc)) from exc

    try:
        user_id = uuid.UUID(payload.subject)
    except ValueError as exc:
        raise _unauthorized("Token subject is not a valid user id.") from exc

    user = db.get(AppUser, user_id)
    if user is None or user.deleted_at is not None:
        raise _unauthorized("Account no longer exists.")
    if user.status != "ACTIVE":
        raise _unauthorized("Account is not active.")

    return user


__all__ = ["get_current_user"]
