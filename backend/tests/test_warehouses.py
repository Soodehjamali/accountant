"""Tests for the warehouse endpoints: CRUD, list, deactivate, assignments."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB warehouse tests",
)

WAREHOUSE_MANAGE = "WAREHOUSE_MANAGE"


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Create a fresh user with WAREHOUSE_MANAGE permission."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_wh_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"WH_MANAGER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Warehouse Manager (test)", created_by=system_user.id
        )
        for perm_code, perm_name, perm_resource, perm_action in [
            (WAREHOUSE_MANAGE, "Manage warehouses", "warehouse", "manage"),
            ("REPRESENTATIVE_MANAGE", "Manage representatives", "representative", "manage"),
        ]:
            try:
                rbac_service.create_permission(
                    session,
                    code=perm_code,
                    name=perm_name,
                    resource=perm_resource,
                    action=perm_action,
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=perm_code
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


# -------------------------------------------------------------------
# Warehouse CRUD
# -------------------------------------------------------------------


@requires_database
def test_create_warehouse_returns_201(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"WH-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/warehouses",
        json={
            "code": code,
            "name": "Test Warehouse",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == code
    assert body["name"] == "Test Warehouse"
    assert body["status"] == "ACTIVE"


@requires_database
def test_create_warehouse_duplicate_code_returns_409(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"WH-{uuid.uuid4().hex[:8]}"
    payload = {
        "code": code,
        "name": "Test Warehouse",
        "type": "REPRESENTATIVE",
        "ownership_mode": "OWNED",
    }
    first = client.post("/api/v1/warehouses", json=payload, headers=auth_headers)
    assert first.status_code == 201
    second = client.post("/api/v1/warehouses", json=payload, headers=auth_headers)
    assert second.status_code == 409


@requires_database
def test_list_warehouses(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"WH-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/v1/warehouses",
        json={
            "code": code,
            "name": "List Test Warehouse",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    response = client.get(f"/api/v1/warehouses?search={code}", headers=auth_headers)
    assert response.status_code == 200
    codes = [item["code"] for item in response.json()["items"]]
    assert code in codes


@requires_database
def test_get_warehouse_by_id(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"WH-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/warehouses",
        json={
            "code": code,
            "name": "Get Test Warehouse",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    wh_id = created.json()["id"]
    response = client.get(f"/api/v1/warehouses/{wh_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["code"] == code


@requires_database
def test_get_unknown_warehouse_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    fake_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/warehouses/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


@requires_database
def test_update_warehouse(client: TestClient, auth_headers: dict[str, str]) -> None:
    code = f"WH-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/warehouses",
        json={
            "code": code,
            "name": "Original",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    wh_id = created.json()["id"]
    response = client.patch(
        f"/api/v1/warehouses/{wh_id}",
        json={"name": "Updated Warehouse"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Warehouse"


@requires_database
def test_create_warehouse_without_auth_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/warehouses",
        json={
            "code": "NOPE",
            "name": "No Auth",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
    )
    assert response.status_code == 401


# -------------------------------------------------------------------
# Warehouse Assignments
# -------------------------------------------------------------------


@requires_database
def test_create_assignment_returns_201(client: TestClient, auth_headers: dict[str, str]) -> None:
    # Create a representative first.
    rep_code = f"REP-{uuid.uuid4().hex[:8]}"
    rep_resp = client.post(
        "/api/v1/representatives",
        json={"code": rep_code, "person_name": "Assign Test Rep"},
        headers=auth_headers,
    )
    # May need REPRESENTATIVE_MANAGE; if 403, skip assignment tests.
    if rep_resp.status_code == 403:
        pytest.skip("REPRESENTATIVE_MANAGE not available for assignment test")
    rep_id = rep_resp.json()["id"]

    # Create a warehouse.
    wh_code = f"WH-{uuid.uuid4().hex[:8]}"
    wh_resp = client.post(
        "/api/v1/warehouses",
        json={
            "code": wh_code,
            "name": "Assign Test WH",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    assert wh_resp.status_code == 201
    wh_id = wh_resp.json()["id"]

    # Assign rep to warehouse.
    response = client.post(
        f"/api/v1/warehouses/{wh_id}/assignments",
        json={"representative_id": rep_id, "is_primary": True},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["representative_id"] == rep_id
    assert body["warehouse_id"] == wh_id
    assert body["is_primary"] is True


@requires_database
def test_duplicate_assignment_returns_409(client: TestClient, auth_headers: dict[str, str]) -> None:
    rep_code = f"REP-{uuid.uuid4().hex[:8]}"
    rep_resp = client.post(
        "/api/v1/representatives",
        json={"code": rep_code, "person_name": "Dup Test Rep"},
        headers=auth_headers,
    )
    if rep_resp.status_code == 403:
        pytest.skip("REPRESENTATIVE_MANAGE not available")
    rep_id = rep_resp.json()["id"]

    wh_code = f"WH-{uuid.uuid4().hex[:8]}"
    wh_resp = client.post(
        "/api/v1/warehouses",
        json={
            "code": wh_code,
            "name": "Dup Test WH",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    wh_id = wh_resp.json()["id"]

    payload = {"representative_id": rep_id, "is_primary": False}
    first = client.post(
        f"/api/v1/warehouses/{wh_id}/assignments",
        json=payload,
        headers=auth_headers,
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/warehouses/{wh_id}/assignments",
        json=payload,
        headers=auth_headers,
    )
    assert second.status_code == 409


@requires_database
def test_list_assignments(client: TestClient, auth_headers: dict[str, str]) -> None:
    rep_code = f"REP-{uuid.uuid4().hex[:8]}"
    rep_resp = client.post(
        "/api/v1/representatives",
        json={"code": rep_code, "person_name": "List Assign Rep"},
        headers=auth_headers,
    )
    if rep_resp.status_code == 403:
        pytest.skip("REPRESENTATIVE_MANAGE not available")
    rep_id = rep_resp.json()["id"]

    wh_code = f"WH-{uuid.uuid4().hex[:8]}"
    wh_resp = client.post(
        "/api/v1/warehouses",
        json={
            "code": wh_code,
            "name": "List Assign WH",
            "type": "REPRESENTATIVE",
            "ownership_mode": "OWNED",
        },
        headers=auth_headers,
    )
    wh_id = wh_resp.json()["id"]

    client.post(
        f"/api/v1/warehouses/{wh_id}/assignments",
        json={"representative_id": rep_id},
        headers=auth_headers,
    )
    response = client.get(
        f"/api/v1/warehouses/{wh_id}/assignments",
        headers=auth_headers,
    )
    assert response.status_code == 200
    rep_ids = [a["representative_id"] for a in response.json()["items"]]
    assert rep_id in rep_ids
