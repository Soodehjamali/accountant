"""Request/response schemas for the auth endpoints (``/api/v1/auth/...``).

Kept independent of the SQLAlchemy ``AppUser`` ORM model (per ``app/
schemas/__init__.py``'s own docstring) -- ``CurrentUserResponse`` is a
deliberately narrow projection of ``AppUser``, not a full serialization of
it (notably: no ``password_hash``, ever, under any field name).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    """Request body for ``POST /auth/login``.

    ``username_or_email`` accepts either credential -- mirrors
    ``services.auth_service.authenticate_user``'s own parameter, which
    looks a submitted value up against both ``AppUser.username`` and
    ``AppUser.email``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"username_or_email": "alice", "password": "hunter2"}
        }
    )

    username_or_email: str
    password: str


class TokenResponse(BaseModel):
    """Response body for a successful ``POST /auth/login``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 28800,
            }
        }
    )

    access_token: str
    #: Always ``"bearer"`` -- present because it's part of the standard
    #: OAuth2 bearer-token response shape (RFC 6750), which is what the
    #: ``Authorization: Bearer <token>`` header on subsequent requests
    #: expects; not because this app supports more than one token_type.
    token_type: str = "bearer"
    #: Seconds until expiry from the moment this response was generated
    #: -- mirrors ``Settings.access_token_expire_minutes`` (in seconds).
    expires_in: int


class CurrentUserResponse(BaseModel):
    """Response body for ``GET /auth/me`` -- the authenticated caller.

    Deliberately excludes ``password_hash`` (obviously) and also
    ``representative_id`` / audit columns -- this endpoint answers "who
    am I" for a client's own UI (e.g. "logged in as Alice"), not a full
    admin-facing user record dump. A future admin "view any user" endpoint
    is a different, separately-authorized concern.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str
    status: str


__all__ = ["CurrentUserResponse", "LoginRequest", "TokenResponse"]
