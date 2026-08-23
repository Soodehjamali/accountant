"""Service layer for RBAC: role/permission definitions, grants, role
assignment, and effective-permission lookup (``role`` R6, ``permission``
R7, ``role_permission`` R8, ``user_role`` M11).

As documented in ``services/__init__.py``, every function here takes an
already-open ``Session`` and never commits/closes it -- that's the
caller's job.

RECONSTRUCTION NOTE: this file did not exist in the uploaded archive even
though ``backend/app/api/v1/endpoints/rbac.py``,
``backend/app/dependencies/rbac.py``, and ``backend/tests/test_rbac.py``
all import and exercise it in detail. It has been rebuilt to satisfy
those three existing files' exact contract (function names, keyword
arguments, and exception types) and the ``Role`` / ``Permission`` /
``RolePermission`` / ``UserRole`` models as written -- please review
before relying on it in production, in particular ``revoke_role``'s
choice to hard-delete the ``user_role`` row (the alternative -- an
``AppendOnlyAuditColumns``-consistent design would instead keep a full
grant/revoke history -- was not specified anywhere in the recovered
files, so the simpler behavior matching the DELETE endpoint's literal
name was chosen).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.permission import Permission
from database.models.role import Role
from database.models.role_permission import RolePermission
from database.models.user_role import UserRole


class DuplicateRoleCodeError(ValueError):
    """Raised when ``create_role`` is called with a ``code`` already in use."""

    def __init__(self, code: str) -> None:
        super().__init__(f"A role with code '{code}' already exists.")
        self.code = code


class DuplicatePermissionCodeError(ValueError):
    """Raised when ``create_permission`` is called with a ``code`` already in use."""

    def __init__(self, code: str) -> None:
        super().__init__(f"A permission with code '{code}' already exists.")
        self.code = code


class RoleNotFoundError(LookupError):
    """Raised when a referenced ``role_code`` has no matching row."""

    def __init__(self, code: str) -> None:
        super().__init__(f"No role with code '{code}' exists.")
        self.code = code


class PermissionNotFoundError(LookupError):
    """Raised when a referenced ``permission_code`` has no matching row."""

    def __init__(self, code: str) -> None:
        super().__init__(f"No permission with code '{code}' exists.")
        self.code = code


class UserNotFoundError(LookupError):
    """Raised when a referenced ``user_id`` has no matching ``AppUser`` row."""

    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__(f"No user with id '{user_id}' exists.")
        self.user_id = user_id


def create_role(
    session: Session, *, code: str, name: str, description: str | None = None, created_by: uuid.UUID
) -> Role:
    """Create and return a new ``Role``.

    ``created_by`` is mandatory — ``Role.created_by`` (UAC) is NOT NULL.

    Raises:
        DuplicateRoleCodeError: if ``code`` is already taken.
    """

    existing = session.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
    if existing is not None:
        raise DuplicateRoleCodeError(code)
    role = Role(code=code, name=name, description=description, created_by=created_by, updated_by=created_by)
    session.add(role)
    session.flush()
    return role


def list_roles(session: Session) -> Iterable[Role]:
    return session.execute(select(Role).order_by(Role.code)).scalars().all()


def create_permission(
    session: Session, *, code: str, name: str, resource: str, action: str, created_by: uuid.UUID
) -> Permission:
    """Create and return a new ``Permission``.

    ``created_by`` is mandatory — ``Permission.created_by`` (UAC) is NOT NULL.

    Raises:
        DuplicatePermissionCodeError: if ``code`` is already taken.
    """

    existing = session.execute(
        select(Permission).where(Permission.code == code)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicatePermissionCodeError(code)
    permission = Permission(code=code, name=name, resource=resource, action=action, created_by=created_by, updated_by=created_by)
    session.add(permission)
    session.flush()
    return permission


def list_permissions(session: Session) -> Iterable[Permission]:
    return session.execute(select(Permission).order_by(Permission.code)).scalars().all()


def _get_role_by_code(session: Session, role_code: str) -> Role:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one_or_none()
    if role is None:
        raise RoleNotFoundError(role_code)
    return role


def _get_permission_by_code(session: Session, permission_code: str) -> Permission:
    permission = session.execute(
        select(Permission).where(Permission.code == permission_code)
    ).scalar_one_or_none()
    if permission is None:
        raise PermissionNotFoundError(permission_code)
    return permission


def grant_permission_to_role(session: Session, *, role_code: str, permission_code: str) -> None:
    """Grant ``permission_code`` to ``role_code``. Idempotent -- granting an
    already-granted permission is a silent no-op, not an error.

    Raises:
        RoleNotFoundError: ``role_code`` doesn't exist.
        PermissionNotFoundError: ``permission_code`` doesn't exist.
    """

    role = _get_role_by_code(session, role_code)
    permission = _get_permission_by_code(session, permission_code)
    existing = session.execute(
        select(RolePermission).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.flush()


def assign_role(
    session: Session,
    *,
    user_id: uuid.UUID,
    role_code: str,
    assigned_by: uuid.UUID | None = None,
) -> None:
    """Assign ``role_code`` to ``user_id``. Idempotent -- re-assigning an
    already-held role is a silent no-op, not an error.

    Raises:
        UserNotFoundError: ``user_id`` doesn't exist.
        RoleNotFoundError: ``role_code`` doesn't exist.
    """

    user = session.get(AppUser, user_id)
    if user is None:
        raise UserNotFoundError(user_id)
    role = _get_role_by_code(session, role_code)
    existing = session.execute(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id)
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(UserRole(user_id=user_id, role_id=role.id, assigned_by=assigned_by))
    session.flush()


def revoke_role(session: Session, *, user_id: uuid.UUID, role_code: str) -> None:
    """Revoke ``role_code`` from ``user_id``, if held. Silent no-op if the
    user never held it.

    Raises:
        RoleNotFoundError: ``role_code`` doesn't exist.
    """

    role = _get_role_by_code(session, role_code)
    existing = session.execute(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id)
    ).scalar_one_or_none()
    if existing is not None:
        session.delete(existing)
        session.flush()


def get_user_permission_codes(session: Session, user_id: uuid.UUID) -> set[str]:
    """Return the set of permission codes ``user_id`` holds via any assigned role."""

    rows = session.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    ).scalars().all()
    return set(rows)


def user_has_permission(session: Session, user_id: uuid.UUID, permission_code: str) -> bool:
    return permission_code in get_user_permission_codes(session, user_id)


__all__ = [
    "DuplicatePermissionCodeError",
    "DuplicateRoleCodeError",
    "PermissionNotFoundError",
    "RoleNotFoundError",
    "UserNotFoundError",
    "assign_role",
    "create_permission",
    "create_role",
    "get_user_permission_codes",
    "grant_permission_to_role",
    "list_permissions",
    "list_roles",
    "revoke_role",
    "user_has_permission",
]
