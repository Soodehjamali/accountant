"""Tests for the audit trail: ``services.audit_service`` and
``/api/v1/audit-log*``.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as the rest of this test suite).
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import audit_service, auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB audit log tests",
)

AUDIT_LOG_VIEW = "AUDIT_LOG_VIEW"


def _login(username: str, password: str) -> dict[str, str]:
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session, username_or_email=username, password=password
        )
        assert user is not None
        session.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session.close()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def viewer_auth_headers() -> dict[str, str]:
    """A fresh user granted ``AUDIT_LOG_VIEW`` via a fresh role."""

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_audit_viewer_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"AUDIT_VIEWER_{suffix}"
        rbac_service.create_role(session, code=role_code, name="Audit Viewer (test)")
        try:
            rbac_service.create_permission(
                session,
                code=AUDIT_LOG_VIEW,
                name="View audit log",
                resource="audit_log",
                action="view",
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=AUDIT_LOG_VIEW
        )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
        )
        session.commit()
    finally:
        session.close()

    return _login(username, password)


@pytest.fixture()
def plain_auth_headers() -> dict[str, str]:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_audit_plain_{suffix}"
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
    return _login(username, password)


@requires_database
def test_record_rejects_invalid_action() -> None:
    session = get_session_factory()()
    try:
        with pytest.raises(audit_service.InvalidAuditActionError):
            audit_service.record(
                session,
                entity_type="customer",
                entity_id=uuid.uuid4(),
                action="NOT_A_REAL_ACTION",
            )
    finally:
        session.close()


@requires_database
def test_record_and_list_roundtrip() -> None:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        entity_id = uuid.uuid4()
        entry = audit_service.record(
            session,
            entity_type="customer",
            entity_id=entity_id,
            action="CREATE",
            actor_user_id=system_user.id,
            after={"name": "Test Co."},
        )
        session.commit()

        results = audit_service.list_entries(session, entity_type="customer", entity_id=entity_id)
        ids = [row.id for row in results]
        assert entry.id in ids
    finally:
        session.close()


@requires_database
def test_list_endpoint_requires_permission(
    client: TestClient, plain_auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/audit-log", headers=plain_auth_headers)
    assert resp.status_code == 403


@requires_database
def test_list_endpoint_returns_entries(
    client: TestClient, viewer_auth_headers: dict[str, str]
) -> None:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        entity_id = uuid.uuid4()
        audit_service.record(
            session,
            entity_type="customer",
            entity_id=entity_id,
            action="CREATE",
            actor_user_id=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    resp = client.get(
        "/api/v1/audit-log",
        params={"entity_type": "customer", "entity_id": str(entity_id)},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 1
    assert body["items"][0]["entity_id"] == str(entity_id)
