"""Service layer for ``app_user`` (M10) authentication.

Authority for the business rules encoded here: ``database/models/
app_user.py``'s own docstring (``status`` vocabulary is ``ACTIVE`` /
``INACTIVE``; soft-deletable via ``deleted_at``).

As documented in ``services/__init__.py``, every function here takes an
already-open ``Session`` and never commits/closes it -- that's the caller's
job (a script, or a FastAPI endpoint via ``app.dependencies.db.get_db``).

Scope note -- what this module deliberately does NOT do:
    It does not decide *who is allowed* to call ``create_user`` (e.g. "only
    an admin role may register new users") -- that's an authorization
    concern for the API layer (a later task, once RBAC/permissions are
    wired up), not this module. ``create_user`` will happily create any
    account it's asked to; gate the *call*, not this function.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from security import hash_password, verify_password


class DuplicateUsernameError(ValueError):
    """Raised when ``create_user`` is called with a ``username`` already in use."""

    def __init__(self, username: str) -> None:
        super().__init__(f"A user with username '{username}' already exists.")
        self.username = username


class DuplicateEmailError(ValueError):
    """Raised when ``create_user`` is called with an ``email`` already in use."""

    def __init__(self, email: str) -> None:
        super().__init__(f"A user with email '{email}' already exists.")
        self.email = email


def create_user(
    session: Session,
    *,
    username: str,
    email: str,
    password: str,
    created_by: uuid.UUID,
    representative_id: uuid.UUID | None = None,
    status: str = "ACTIVE",
) -> AppUser:
    """Create and return a new ``AppUser`` with a securely hashed password.

    ``password`` is the plaintext password -- it is hashed here (via
    ``app.core.security.hash_password``) before ever reaching the ORM
    object or the database; the plaintext itself is never persisted or
    logged.

    When ``representative_id`` is provided the ``REPRESENTATIVE`` role
    (which carries ``BOT_QUERY``) is automatically assigned to the new
    user so that the representative can immediately use bot read
    endpoints.  Non-representative users (admins, accountants, etc.)
    are unaffected.

    Raises:
        DuplicateUsernameError: if ``username`` is already taken.
        DuplicateEmailError: if ``email`` is already taken.
    """

    existing_username = session.execute(
        select(AppUser).where(AppUser.username == username)
    ).scalar_one_or_none()
    if existing_username is not None:
        raise DuplicateUsernameError(username)

    existing_email = session.execute(
        select(AppUser).where(AppUser.email == email)
    ).scalar_one_or_none()
    if existing_email is not None:
        raise DuplicateEmailError(email)

    user = AppUser(
        username=username,
        email=email,
        password_hash=hash_password(password),
        status=status,
        representative_id=representative_id,
        created_by=created_by,
    )
    session.add(user)
    session.flush()  # populate user.id / server defaults before return

    # Auto-assign the REPRESENTATIVE role (BOT_QUERY) when the user is
    # linked to a Representative.  ``rbac_service.assign_role`` is
    # idempotent -- safe if the role was already granted.
    if representative_id is not None:
        from services import rbac_service

        rbac_service.assign_role(
            session,
            user_id=user.id,
            role_code="REPRESENTATIVE",
            assigned_by=created_by,
        )

    return user


def authenticate_user(
    session: Session, *, username_or_email: str, password: str
) -> AppUser | None:
    """Verify credentials and return the matching ``AppUser``, or ``None``.

    Returns ``None`` (never raises) for every failure case -- wrong
    password, unknown username/email, ``INACTIVE`` status, or a
    soft-deleted account -- so a caller (the future login endpoint) can't
    accidentally leak *which* of those was true via a different error
    shape. That distinction matters for security (e.g. not confirming
    whether a given email is registered) and belongs to the API layer to
    decide how much to reveal, not this function.

    On success, stamps ``last_login_at`` to now (UTC) and flushes -- the
    caller still owns the final ``commit()``.
    """

    user = session.execute(
        select(AppUser).where(
            or_(
                AppUser.username == username_or_email,
                AppUser.email == username_or_email,
            ),
            AppUser.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if user is None:
        return None
    if user.status != "ACTIVE":
        return None
    if not verify_password(password, user.password_hash):
        return None

    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return user


__all__ = [
    "DuplicateEmailError",
    "DuplicateUsernameError",
    "authenticate_user",
    "create_user",
]
