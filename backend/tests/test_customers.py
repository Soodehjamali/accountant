"""Tests for the customer endpoints: ``POST/GET /customers``,
``GET/PATCH /customers/{id}``, ``POST /customers/{id}/deactivate``.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_products.py`` / ``test_rbac.py``). Mirrors
``test_rbac.py``'s pattern of a permission-holding fixture
(``manage_auth_headers``, granted ``CUSTOMER_MANAGE``) plus a
no-permissions fixture, to prove the write endpoints are actually gated.
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
    reason="DATABASE_URL is not set; skipping live DB customer tests",
)

CUSTOMER_MANAGE = "CUSTOMER_MANAGE"


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
def manage_auth_headers() -> dict[str, str]:
    """A fresh user granted ``CUSTOMER_MANAGE`` via a fresh role -- proves
    the write endpoints work for a properly-permissioned caller."""

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_cust_mgr_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"CUSTOMER_MANAGER_{suffix}"
        rbac_service.create_role(session, code=role_code, name="Customer Manager (test)", created_by=system_user.id)
        try:
            rbac_service.create_permission(
                session,
                code=CUSTOMER_MANAGE,
                name="Manage customers",
                resource="customer",
                action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=CUSTOMER_MANAGE
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
    """A fresh user with no roles -- proves write endpoints reject them."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_cust_plain_{suffix}"
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


@pytest.fixture()
def default_currency_id() -> str:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        session.commit()
        return str(currency.id)
    finally:
        session.close()


@requires_database
def test_create_and_get_customer(
    client: TestClient, manage_auth_headers: dict[str, str], default_currency_id: str
) -> None:
    code = f"CUST-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/customers",
        json={
            "code": code,
            "name": "Test Customer Co.",
            "type": "CORPORATE",
            "currency_id": default_currency_id,
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"] == code
    assert body["status"] == "ACTIVE"

    get_resp = client.get(f"/api/v1/customers/{body['id']}", headers=manage_auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == body["id"]


@requires_database
def test_create_customer_requires_permission(
    client: TestClient, plain_auth_headers: dict[str, str], default_currency_id: str
) -> None:
    resp = client.post(
        "/api/v1/customers",
        json={
            "code": f"CUST-{uuid.uuid4().hex[:8]}",
            "name": "Should Fail",
            "type": "INDIVIDUAL",
            "currency_id": default_currency_id,
        },
        headers=plain_auth_headers,
    )
    assert resp.status_code == 403


@requires_database
def test_duplicate_code_rejected(
    client: TestClient, manage_auth_headers: dict[str, str], default_currency_id: str
) -> None:
    code = f"CUST-{uuid.uuid4().hex[:8]}"
    payload = {
        "code": code,
        "name": "First",
        "type": "INDIVIDUAL",
        "currency_id": default_currency_id,
    }
    first = client.post("/api/v1/customers", json=payload, headers=manage_auth_headers)
    assert first.status_code == 201

    second = client.post("/api/v1/customers", json=payload, headers=manage_auth_headers)
    assert second.status_code == 409


@requires_database
def test_deactivate_customer(
    client: TestClient, manage_auth_headers: dict[str, str], default_currency_id: str
) -> None:
    code = f"CUST-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/customers",
        json={
            "code": code,
            "name": "To Deactivate",
            "type": "INDIVIDUAL",
            "currency_id": default_currency_id,
        },
        headers=manage_auth_headers,
    ).json()

    resp = client.post(
        f"/api/v1/customers/{created['id']}/deactivate", headers=manage_auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "INACTIVE"
