"""Tests for reference-data endpoints: ``GET /units-of-measure``, ``GET /product-categories``.

Skipped automatically if ``DATABASE_URL`` is not configured in the test
environment (same convention as ``test_products.py``).

These endpoints are read-only and require only authentication (no
special permission), so the ``auth_headers`` fixture here is simpler
than the one in ``test_products.py``.
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
    reason="DATABASE_URL is not set; skipping live DB reference-data tests",
)


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Create a fresh ``ACTIVE`` ``AppUser``, log in, and return auth headers."""

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_refdata_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
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


# ---------------------------------------------------------------------------
# GET /api/v1/units-of-measure
# ---------------------------------------------------------------------------

@requires_database
def test_list_units_of_measure_returns_200(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The seeded default UoM should always be present."""
    response = client.get("/api/v1/units-of-measure", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) >= 1  # at least the seeded default
    # Every item must have the expected shape
    item = body["items"][0]
    for key in ("id", "code", "name", "class_"):
        assert key in item, f"Missing key '{key}' in UoM response"


@requires_database
def test_list_units_of_measure_without_auth_returns_401(
    client: TestClient
) -> None:
    response = client.get("/api/v1/units-of-measure")
    assert response.status_code == 401


@requires_database
def test_list_units_of_measure_filter_by_class(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Filtering by ``class_=BASE`` should still return valid results."""
    response = client.get(
        "/api/v1/units-of-measure",
        params={"class_": "BASE"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    for item in body["items"]:
        assert item["class_"] == "BASE"


# ---------------------------------------------------------------------------
# GET /api/v1/product-categories
# ---------------------------------------------------------------------------

@requires_database
def test_list_product_categories_returns_200(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Should return a (possibly empty) list of categories."""
    response = client.get("/api/v1/product-categories", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    # Every item must have the expected shape
    if body["items"]:
        item = body["items"][0]
        for key in ("id", "code", "name", "parent_category_id", "level"):
            assert key in item, f"Missing key '{key}' in category response"


@requires_database
def test_list_product_categories_without_auth_returns_401(
    client: TestClient
) -> None:
    response = client.get("/api/v1/product-categories")
    assert response.status_code == 401
