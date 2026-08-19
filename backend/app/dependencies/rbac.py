"""FastAPI dependency factory for permission-gated endpoints.

Layers on top of ``get_current_user`` (``app/dependencies/auth.py``):
that dependency answers "is this caller authenticated at all";
``require_permission`` answers "is this caller *authorized* for this
specific action". Usage on a protected endpoint::

    @router.post("/roles/{role_code}/permissions/{permission_code}")
    def grant(
        role_code: str,
        permission_code: str,
        db: Session = Depends(get_db),
        _: AppUser = Depends(require_permission("RBAC_MANAGE")),
    ) -> ...:
        ...

Every earlier endpoint module (``products.py``, ``inventory.py``) notes
in its own docstring that it only checks "logged in", not "authorized for
this action", because RBAC wasn't wired up yet. This dependency is that
missing piece -- existing endpoints are deliberately left unchanged here
(narrowing them is a follow-up, since it changes their access contract)
but any *new* endpoint, and the RBAC admin endpoints this same task adds,
use it from the start.
"""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from database.models.app_user import AppUser
from services import rbac_service


def require_permission(permission_code: str) -> Callable[..., AppUser]:
    """Return a FastAPI dependency that requires the caller to hold
    ``permission_code`` (via any assigned role), raising HTTP 403 otherwise.

    A factory (rather than a single dependency function) because each
    protected endpoint needs a *different* permission code baked in --
    mirrors the standard FastAPI "parameterized dependency" pattern.
    """

    def _dependency(
        current_user: AppUser = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AppUser:
        if not rbac_service.user_has_permission(db, current_user.id, permission_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission '{permission_code}'.",
            )
        return current_user

    return _dependency


__all__ = ["require_permission"]
