"""Tests for PriceList and PriceHistory endpoints."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB price list tests",
)

PRICE_LIST_MANAGE = "PRICE_LIST_MANAGE"


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Create a fresh user with PRICE_LIST_MANAGE permission."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_pl_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"PL_MANAGER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Price List Manager (test)", created_by=system_user.id
        )
        try:
            rbac_service.create_permission(
                session,
                code=PRICE_LIST_MANAGE,
                name="Manage price lists",
                resource="price_list",
                action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=PRICE_LIST_MANAGE
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


def _seed_currency_and_product(session, auth_user_id):
    """Ensure default currency and a test product exist, return (currency_id, product_id)."""
    currency = bootstrap_service.ensure_default_currency(session, actor_id=auth_user_id)
    bootstrap_service.ensure_default_uom(session, actor_id=auth_user_id)

    from database.models.product import Product
    from sqlalchemy import select

    existing = session.execute(
        select(Product).where(Product.sku == "TESTPL-001")
    ).scalar_one_or_none()
    if existing is not None:
        return str(currency.id), str(existing.id)

    product = Product(
        sku="TESTPL-001",
        name="Test Price List Product",
        base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=auth_user_id).id,
        status="ACTIVE",
        created_by=auth_user_id,
        updated_by=auth_user_id,
    )
    session.add(product)
    session.flush()
    return str(currency.id), str(product.id)


# -------------------------------------------------------------------
# Price List CRUD
# -------------------------------------------------------------------


@requires_database
def test_create_price_list_returns_201(client: TestClient, auth_headers: dict[str, str]) -> None:
    name = f"PL-{uuid.uuid4().hex[:8]}"
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/v1/price-lists",
        json={
            "name": name,
            "price_type": "RETAIL",
            "currency_id": currency_id,
            "owner_scope": "General",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == name
    assert body["price_type"] == "RETAIL"
    assert body["is_active"] is True


@requires_database
def test_create_price_list_duplicate_name_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    name = f"PL-{uuid.uuid4().hex[:8]}"
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    payload = {
        "name": name,
        "price_type": "RETAIL",
        "currency_id": currency_id,
        "owner_scope": "General",
    }
    first = client.post("/api/v1/price-lists", json=payload, headers=auth_headers)
    assert first.status_code == 201
    second = client.post("/api/v1/price-lists", json=payload, headers=auth_headers)
    assert second.status_code == 409


@requires_database
def test_list_price_lists(client: TestClient, auth_headers: dict[str, str]) -> None:
    name = f"PL-{uuid.uuid4().hex[:8]}"
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    client.post(
        "/api/v1/price-lists",
        json={
            "name": name,
            "price_type": "RETAIL",
            "currency_id": currency_id,
            "owner_scope": "List Test",
        },
        headers=auth_headers,
    )
    response = client.get(f"/api/v1/price-lists?search={name}", headers=auth_headers)
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert name in names


@requires_database
def test_get_price_list_by_id(client: TestClient, auth_headers: dict[str, str]) -> None:
    name = f"PL-{uuid.uuid4().hex[:8]}"
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    created = client.post(
        "/api/v1/price-lists",
        json={
            "name": name,
            "price_type": "WHOLESALE",
            "currency_id": currency_id,
            "owner_scope": "Get Test",
        },
        headers=auth_headers,
    )
    pl_id = created.json()["id"]
    response = client.get(f"/api/v1/price-lists/{pl_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == name


@requires_database
def test_get_unknown_price_list_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    fake_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/price-lists/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


@requires_database
def test_update_price_list(client: TestClient, auth_headers: dict[str, str]) -> None:
    name = f"PL-{uuid.uuid4().hex[:8]}"
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    created = client.post(
        "/api/v1/price-lists",
        json={
            "name": name,
            "price_type": "RETAIL",
            "currency_id": currency_id,
            "owner_scope": "Original",
        },
        headers=auth_headers,
    )
    pl_id = created.json()["id"]
    response = client.patch(
        f"/api/v1/price-lists/{pl_id}",
        json={"owner_scope": "Updated"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["owner_scope"] == "Updated"


@requires_database
def test_deactivate_and_activate_price_list(client: TestClient, auth_headers: dict[str, str]) -> None:
    name = f"PL-{uuid.uuid4().hex[:8]}"
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    created = client.post(
        "/api/v1/price-lists",
        json={
            "name": name,
            "price_type": "RETAIL",
            "currency_id": currency_id,
            "owner_scope": "Toggle Test",
        },
        headers=auth_headers,
    )
    pl_id = created.json()["id"]
    assert created.json()["is_active"] is True

    # Deactivate.
    resp = client.post(f"/api/v1/price-lists/{pl_id}/deactivate", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Activate.
    resp = client.post(f"/api/v1/price-lists/{pl_id}/activate", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


@requires_database
def test_create_price_list_without_auth_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/price-lists",
        json={
            "name": "NOPE",
            "price_type": "RETAIL",
            "currency_id": str(uuid.uuid4()),
            "owner_scope": "No Auth",
        },
    )
    assert response.status_code == 401


# -------------------------------------------------------------------
# Price Entries
# -------------------------------------------------------------------


def _create_price_list(client, auth_headers, currency_id, suffix=""):
    """Helper to create a price list and return its id."""
    name = f"PL-{suffix}{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/price-lists",
        json={
            "name": name,
            "price_type": "RETAIL",
            "currency_id": currency_id,
            "owner_scope": "Entry Test",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"], name


@requires_database
def test_add_price_entry_returns_201(client: TestClient, auth_headers: dict[str, str]) -> None:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, product_id = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    pl_id, _ = _create_price_list(client, auth_headers, currency_id)

    response = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 100.00,
            "effective_from": "2026-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["product_id"] == product_id
    assert float(body["unit_price"]) == 100.00
    assert body["effective_to"] is None


@requires_database
def test_add_price_entry_to_inactive_list_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, product_id = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    pl_id, _ = _create_price_list(client, auth_headers, currency_id, suffix="inactive-")
    # Deactivate.
    client.post(f"/api/v1/price-lists/{pl_id}/deactivate", headers=auth_headers)

    response = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 50.00,
            "effective_from": "2026-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert response.status_code == 409


@requires_database
def test_add_price_entry_nonexistent_product_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, _ = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    pl_id, _ = _create_price_list(client, auth_headers, currency_id, suffix="nofound-")

    response = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": str(uuid.uuid4()),
            "unit_price": 50.00,
            "effective_from": "2026-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404


@requires_database
def test_overlapping_price_returns_409(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Adding a price entry whose effective_from falls before an existing
    open entry's effective_from should be rejected as overlapping — the
    new entry would conflict with the already-open window."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, product_id = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    pl_id, _ = _create_price_list(client, auth_headers, currency_id, suffix="overlap-")

    # First entry at 2026-01-01 (open-ended).
    resp1 = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 100.00,
            "effective_from": "2026-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert resp1.status_code == 201

    # Try to add an entry BEFORE the first one — this should be rejected
    # because it would overlap with the existing open window.
    resp = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 50.00,
            "effective_from": "2025-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 409


@requires_database
def test_price_history_preserved_after_update(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """A new price version closes the previous and both rows are preserved."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, product_id = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    pl_id, _ = _create_price_list(client, auth_headers, currency_id, suffix="hist-")

    # First version — open-ended.
    resp1 = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 100.00,
            "effective_from": "2026-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert resp1.status_code == 201
    entry1_id = resp1.json()["id"]

    # Second version in the future — closes the first one.
    resp2 = client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 150.00,
            "effective_from": "2027-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert resp2.status_code == 201
    entry2 = resp2.json()

    # Re-read first entry to check effective_to was set.
    resp1_reread = client.get(
        f"/api/v1/price-lists/{pl_id}/items/{entry1_id}",
        headers=auth_headers,
    )
    assert resp1_reread.status_code == 200
    entry1 = resp1_reread.json()

    # First entry should now have effective_to set (closed by second).
    assert entry1["effective_to"] is not None
    # Second entry should have no effective_to (currently open).
    assert entry2["effective_to"] is None

    # Both entries should be listed.
    resp = client.get(f"/api/v1/price-lists/{pl_id}/items", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()["items"]
    assert len(entries) == 2
    prices = sorted([float(e["unit_price"]) for e in entries])
    assert prices == [100.00, 150.00]


@requires_database
def test_list_price_entries_filtered_by_product(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency_id, product_id = _seed_currency_and_product(session, system_user.id)
        session.commit()
    finally:
        session.close()

    pl_id, _ = _create_price_list(client, auth_headers, currency_id, suffix="filter-")

    client.post(
        f"/api/v1/price-lists/{pl_id}/items",
        json={
            "product_id": product_id,
            "unit_price": 100.00,
            "effective_from": "2026-01-01T00:00:00Z",
        },
        headers=auth_headers,
    )

    # Filter by product_id.
    resp = client.get(
        f"/api/v1/price-lists/{pl_id}/items?product_id={product_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    # Filter by non-matching product_id.
    resp = client.get(
        f"/api/v1/price-lists/{pl_id}/items?product_id={uuid.uuid4()}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 0
