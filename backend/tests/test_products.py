"""Tests for the product endpoints: ``POST/GET /products``, ``GET /products/{sku}``.

Skipped automatically if ``DATABASE_URL`` is not configured in the test
environment (same convention as ``test_auth.py``/``test_db_health.py``) --
these exercise the real ``services.product_service`` against a real
database, not a mock.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB product tests",
)


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Create a fresh, ``ACTIVE`` ``AppUser``, log in, and return auth headers.

    Every product endpoint requires an authenticated caller, so tests need
    a real bearer token -- obtained here the same way
    ``test_auth.py``'s ``seeded_user`` fixture creates its user, plus an
    extra login round-trip through the real ``/auth/login`` endpoint logic
    (via ``security.create_access_token`` directly would also work, but
    going through ``auth_service.authenticate_user`` keeps this fixture
    honest about what a real client would present).
    """

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_products_{suffix}"
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


@pytest.fixture()
def default_uom_id(auth_headers: dict[str, str]) -> str:
    """Return the seeded default UoM's id, creating it if absent."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        session.commit()
        return str(uom.id)
    finally:
        session.close()


@requires_database
def test_create_product_returns_201(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    sku = f"TEST-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Test Widget", "base_uom_id": default_uom_id},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sku"] == sku
    assert body["status"] == "ACTIVE"
    assert body["name"] == "Test Widget"


@requires_database
def test_create_product_without_auth_returns_401(
    client: TestClient, default_uom_id: str
) -> None:
    response = client.post(
        "/api/v1/products",
        json={"sku": "SHOULD-FAIL", "name": "Nope", "base_uom_id": default_uom_id},
    )
    assert response.status_code == 401


@requires_database
def test_create_product_with_duplicate_sku_returns_409(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    sku = f"TEST-{uuid.uuid4().hex[:8]}"
    payload = {"sku": sku, "name": "Test Widget", "base_uom_id": default_uom_id}
    first = client.post("/api/v1/products", json=payload, headers=auth_headers)
    assert first.status_code == 201

    second = client.post("/api/v1/products", json=payload, headers=auth_headers)
    assert second.status_code == 409


@requires_database
def test_list_products_returns_created_product(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    sku = f"TEST-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Test Widget", "base_uom_id": default_uom_id},
        headers=auth_headers,
    )

    response = client.get("/api/v1/products", headers=auth_headers)
    assert response.status_code == 200
    skus = [item["sku"] for item in response.json()["items"]]
    assert sku in skus


@requires_database
def test_get_product_by_sku_returns_200(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    sku = f"TEST-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Test Widget", "base_uom_id": default_uom_id},
        headers=auth_headers,
    )

    response = client.get(f"/api/v1/products/{sku}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["sku"] == sku


@requires_database
def test_get_unknown_product_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/products/NO-SUCH-SKU", headers=auth_headers
    )
    assert response.status_code == 404
