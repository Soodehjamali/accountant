"""Authentication endpoints: ``POST /auth/login``, ``GET /auth/me``.

The first business (non-health) endpoints in this backend -- everything
here is a thin HTTP wrapper around ``services.auth_service``, per this
project's layering rule (``services/__init__.py``'s docstring): business
rules live in ``services/``, never duplicated here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse
from database.models.app_user import AppUser
from security import create_access_token
from services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Obtain an access token")
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Verify credentials and return a signed access token.

    Returns HTTP 401 for any credential failure (unknown user, wrong
    password, inactive/deleted account) -- deliberately the same error
    for all of them (see ``auth_service.authenticate_user``'s own
    docstring on why: not revealing *which* part of the credential pair
    was wrong is a basic login-endpoint hardening practice).
    """

    user = auth_service.authenticate_user(
        db, username_or_email=body.username_or_email, password=body.password
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password.",
        )
    db.commit()  # persist the last_login_at stamp authenticate_user() set

    settings = get_settings()
    expires_in_seconds = settings.access_token_expire_minutes * 60
    token = create_access_token(
        subject=str(user.id),
        secret_key=settings.secret_key,
        expires_in_seconds=expires_in_seconds,
    )
    return TokenResponse(access_token=token, expires_in=expires_in_seconds)


@router.get("/me", response_model=CurrentUserResponse, summary="Get the current user")
def read_current_user(
    current_user: AppUser = Depends(get_current_user),
) -> CurrentUserResponse:
    """Return the profile of whoever the Bearer token identifies.

    The ``portal`` field is derived server-side from whether the user is
    linked to a ``Representative``, giving the frontend exactly the
    routing hint it needs without leaking the raw linkage.
    """

    return CurrentUserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        status=current_user.status,
        portal="representative" if current_user.representative_id else "office",
    )


__all__ = ["router"]
