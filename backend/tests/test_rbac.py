"""Tests for RBAC: ``services.rbac_service`` and ``/api/v1/rbac/*``.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as the rest of this test suite).
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB RBAC tests",
)


@pytest.fixture()
def admin_auth_headers() -> dict[str, str]:
    """Log in as the seeded system user (bootstrapped onto ADMIN /
    RBAC_MANAGE by ``ensure_rbac_bootstrap``) and return auth headers.

    The system user has no usable password (see
    ``bootstrap_service.ensure_system_user``'s own docstring: a
    placeholder hash, not a real login flow), so this creates a *second*
    fresh user, assigns it the ADMIN role directly via the service layer
    (bypassing the API, since that's what's under test), and logs in as
    that one instead -- proves role-assignment plus permission-gated
    access end-to-end without depending on the system account's own
    non-functional password.
    """

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_admin_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        rbac_service.assign_role(
            session,
            user_id=new_user.id,
            role_code=bootstrap_service.ADMIN_ROLE_CODE,
            assigned_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session2 = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session2, username_or_email=username, password=password
        )
        assert user is not None
        session2.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session2.close()

    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def plain_auth_headers() -> dict[str, str]:
    """Log in as a fresh user with no roles at all -- used to prove
    permission-gated endpoints reject callers who lack RBAC_MANAGE."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_plain_{suffix}"
        password = "correct-horse-battery-staple"
        auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session2 = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session2, username_or_email=username, password=password
        )
        assert user is not None
        session2.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session2.close()

    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------- service layer


@requires_database
def test_grant_and_check_permission() -> None:
    session = get_session_factory()()
    try:
        suffix = uuid.uuid4().hex[:8]
        system_user = bootstrap_service.ensure_system_user(session)
        role = rbac_service.create_role(session, code=f"ROLE_{suffix}", name="Test Role", created_by=system_user.id)
        permission = rbac_service.create_permission(
            session,
            code=f"PERM_{suffix}",
            name="Test Permission",
            resource="test",
            action="do",
            created_by=system_user.id,
        )
        rbac_service.grant_permission_to_role(
            session, role_code=role.code, permission_code=permission.code
        )

        system_user = bootstrap_service.ensure_system_user(session)
        new_user = auth_service.create_user(
            session,
            username=f"test_rbac_{suffix}",
            email=f"test_rbac_{suffix}@example.invalid",
            password="correct-horse-battery-staple",
            created_by=system_user.id,
        )
        assert not rbac_service.user_has_permission(session, new_user.id, permission.code)

        rbac_service.assign_role(session, user_id=new_user.id, role_code=role.code)
        session.commit()

        assert rbac_service.user_has_permission(session, new_user.id, permission.code)

        rbac_service.revoke_role(session, user_id=new_user.id, role_code=role.code)
        session.commit()
        assert not rbac_service.user_has_permission(session, new_user.id, permission.code)
    finally:
        session.close()


@requires_database
def test_create_role_with_duplicate_code_raises() -> None:
    session = get_session_factory()()
    try:
        suffix = uuid.uuid4().hex[:8]
        system_user = bootstrap_service.ensure_system_user(session)
        rbac_service.create_role(session, code=f"DUP_{suffix}", name="First", created_by=system_user.id)
        session.commit()
        with pytest.raises(rbac_service.DuplicateRoleCodeError):
            rbac_service.create_role(session, code=f"DUP_{suffix}", name="Second", created_by=system_user.id)
    finally:
        session.close()


# ------------------------------------------------------------------- API


@requires_database
def test_api_create_role_requires_rbac_manage(
    client: TestClient, plain_auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/rbac/roles",
        json={"code": f"NOPE_{uuid.uuid4().hex[:8]}", "name": "Nope"},
        headers=plain_auth_headers,
    )
    assert response.status_code == 403


@requires_database
def test_api_admin_can_create_role_and_permission_and_grant(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"API_ROLE_{suffix}"
    permission_code = f"API_PERM_{suffix}"

    role_response = client.post(
        "/api/v1/rbac/roles",
        json={"code": role_code, "name": "API Test Role"},
        headers=admin_auth_headers,
    )
    assert role_response.status_code == 201

    permission_response = client.post(
        "/api/v1/rbac/permissions",
        json={
            "code": permission_code,
            "name": "API Test Permission",
            "resource": "test",
            "action": "do",
        },
        headers=admin_auth_headers,
    )
    assert permission_response.status_code == 201

    grant_response = client.post(
        f"/api/v1/rbac/roles/{role_code}/permissions/{permission_code}",
        headers=admin_auth_headers,
    )
    assert grant_response.status_code == 204

    roles_response = client.get("/api/v1/rbac/roles", headers=admin_auth_headers)
    assert role_code in [r["code"] for r in roles_response.json()["items"]]


@requires_database
def test_api_me_permissions_reflects_assigned_role(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/rbac/me/permissions", headers=admin_auth_headers)
    assert response.status_code == 200
    assert bootstrap_service.RBAC_MANAGE_PERMISSION_CODE in response.json()["permission_codes"]
