"""Tests for the representative endpoints: CRUD, list, deactivate."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB representative tests",
)

REPRESENTATIVE_MANAGE = "REPRESENTATIVE_MANAGE"


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Create a fresh user with REPRESENTATIVE_MANAGE permission."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_rep_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"REP_MANAGER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Rep Manager (test)", created_by=system_user.id
        )
        try:
            rbac_service.create_permission(
                session,
                code=REPRESENTATIVE_MANAGE,
                name="Manage representatives",
                resource="representative",
                action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=REPRESENTATIVE_MANAGE
        )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
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


@requires_database
def test_create_representative_returns_201(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"REP-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/representatives",
        json={"code": code, "person_name": "John Doe"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == code
    assert body["person_name"] == "John Doe"
    assert body["status"] == "ACTIVE"


@requires_database
def test_create_representative_duplicate_code_returns_409(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"REP-{uuid.uuid4().hex[:8]}"
    payload = {"code": code, "person_name": "John Doe"}
    first = client.post("/api/v1/representatives", json=payload, headers=auth_headers)
    assert first.status_code == 201
    second = client.post("/api/v1/representatives", json=payload, headers=auth_headers)
    assert second.status_code == 409


@requires_database
def test_list_representatives(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"REP-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/v1/representatives",
        json={"code": code, "person_name": "List Test"},
        headers=auth_headers,
    )
    response = client.get(f"/api/v1/representatives?search={code}", headers=auth_headers)
    assert response.status_code == 200
    codes = [item["code"] for item in response.json()["items"]]
    assert code in codes


@requires_database
def test_get_representative_by_id(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"REP-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/representatives",
        json={"code": code, "person_name": "Get Test"},
        headers=auth_headers,
    )
    rep_id = created.json()["id"]
    response = client.get(f"/api/v1/representatives/{rep_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["code"] == code


@requires_database
def test_get_unknown_representative_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    fake_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/representatives/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


@requires_database
def test_update_representative(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"REP-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/representatives",
        json={"code": code, "person_name": "Original"},
        headers=auth_headers,
    )
    rep_id = created.json()["id"]
    response = client.patch(
        f"/api/v1/representatives/{rep_id}",
        json={"person_name": "Updated"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["person_name"] == "Updated"


@requires_database
def test_deactivate_representative(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"REP-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/representatives",
        json={"code": code, "person_name": "Deactivate Me"},
        headers=auth_headers,
    )
    rep_id = created.json()["id"]
    response = client.post(
        f"/api/v1/representatives/{rep_id}/deactivate",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "OFFBOARDED"


@requires_database
def test_create_representative_without_auth_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/representatives",
        json={"code": "NOPE", "person_name": "No Auth"},
    )
    assert response.status_code == 401
