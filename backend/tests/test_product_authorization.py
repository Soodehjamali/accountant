"""Tests for product authorization: ``POST /api/v1/products`` permission gate.

Covers:
1. Authorized user (PRODUCT_MANAGE) can create a product.
2. Authenticated user WITHOUT PRODUCT_MANAGE cannot create (403).
3. Unauthenticated request is rejected (401).
4. Rejected create request causes zero database side effects.
5. Admin/staff user with PRODUCT_MANAGE can create (follows RBAC convention).
6. Existing product read behavior remains unchanged (GET /products, GET /products/{sku}).
7. Products are global (not representative-scoped) -- no scope enforcement needed.

All tests use real PostgreSQL (same skipif convention as other test files).
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models.product import Product
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping product authorization tests",
)

PRODUCT_MANAGE = "PRODUCT_MANAGE"


def _login(username: str, password: str) -> dict[str, str]:
    """Log in and return auth headers."""
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
    """A fresh user granted PRODUCT_MANAGE via a fresh role -- proves
    the create endpoint works for a properly-permissioned caller."""

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_prod_mgr_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"PRODUCT_MANAGER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Product Manager (test)", created_by=system_user.id
        )
        try:
            rbac_service.create_permission(
                session,
                code=PRODUCT_MANAGE,
                name="Manage products",
                resource="product",
                action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=PRODUCT_MANAGE
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
    """A fresh user with no roles -- proves create endpoint rejects them."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_prod_plain_{suffix}"
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
def admin_auth_headers() -> dict[str, str]:
    """An admin user (holds ADMIN role which includes PRODUCT_MANAGE)."""

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_prod_admin_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        # Grant ADMIN role (which includes PRODUCT_MANAGE via bootstrap)
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code="ADMIN", assigned_by=system_user.id
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@pytest.fixture()
def default_uom_id() -> str:
    """Return the seeded default UoM's id."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        session.commit()
        return str(uom.id)
    finally:
        session.close()


@requires_database
def test_authorized_user_can_create_product(
    client: TestClient, manage_auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Authorized user with PRODUCT_MANAGE can create a product (201)."""
    sku = f"AUTH-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Authorized Product", "base_uom_id": default_uom_id},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sku"] == sku
    assert body["status"] == "ACTIVE"
    assert body["name"] == "Authorized Product"


@requires_database
def test_unauthorized_user_cannot_create_product(
    client: TestClient, plain_auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Authenticated user WITHOUT PRODUCT_MANAGE is rejected (403)."""
    sku = f"UNAUTH-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Should Fail", "base_uom_id": default_uom_id},
        headers=plain_auth_headers,
    )
    assert resp.status_code == 403
    assert "PRODUCT_MANAGE" in resp.json()["detail"]


@requires_database
def test_unauthenticated_request_is_rejected(
    client: TestClient, default_uom_id: str
) -> None:
    """Unauthenticated request is rejected (401)."""
    resp = client.post(
        "/api/v1/products",
        json={"sku": "NOAUTH", "name": "No Auth", "base_uom_id": default_uom_id},
    )
    assert resp.status_code == 401


@requires_database
def test_rejected_create_causes_no_side_effects(
    client: TestClient, plain_auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Rejected create request causes zero database side effects."""
    sku = f"NOFX-{uuid.uuid4().hex[:8]}"

    # Count products before
    session = get_session_factory()()
    try:
        before = len(
            session.execute(select(Product).where(Product.sku == sku)).scalars().all()
        )
    finally:
        session.close()

    # Attempt (should be 403)
    resp = client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Should Fail", "base_uom_id": default_uom_id},
        headers=plain_auth_headers,
    )
    assert resp.status_code == 403

    # Verify no new product was created
    session = get_session_factory()()
    try:
        after = len(
            session.execute(select(Product).where(Product.sku == sku)).scalars().all()
        )
        assert after == before, "No product should have been created for unauthorized request"
    finally:
        session.close()


@requires_database
def test_admin_user_can_create_product(
    client: TestClient, admin_auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Admin user with ADMIN role (which includes PRODUCT_MANAGE) can create."""
    sku = f"ADMIN-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Admin Product", "base_uom_id": default_uom_id},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["sku"] == sku


@requires_database
def test_list_products_read_unchanged(
    client: TestClient, plain_auth_headers: dict[str, str]
) -> None:
    """GET /products still works with only authentication (no permission required)."""
    resp = client.get("/api/v1/products", headers=plain_auth_headers)
    assert resp.status_code == 200
    assert "items" in resp.json()


@requires_database
def test_get_product_by_sku_read_unchanged(
    client: TestClient, manage_auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """GET /products/{sku} still works with only authentication."""
    sku = f"READ-{uuid.uuid4().hex[:8]}"
    # Create via authorized user
    client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Read Test", "base_uom_id": default_uom_id},
        headers=manage_auth_headers,
    )
    # Read by any authenticated user
    from app.core.config import get_settings
    from security import create_access_token

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_prod_reader_{suffix}"
        password = "correct-horse-battery-staple"
        auth_service.create_user(
            session, username=username, email=f"{username}@example.invalid",
            password=password, created_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    reader_headers = _login(username, password)
    resp = client.get(f"/api/v1/products/{sku}", headers=reader_headers)
    assert resp.status_code == 200
    assert resp.json()["sku"] == sku
