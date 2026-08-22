"""Tests for the order endpoints: ``POST /orders`` through the ADR-004
lifecycle (submit/approve/reserve/start-fulfillment/ship/cancel/etc).

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_rbac.py``). Builds its own
supporting rows (currency, warehouse, uom, product, price_history,
representative, customer) directly via the ORM/service layer, the same
way ``test_customers.py`` uses ``bootstrap_service`` for its currency
fixture -- no dedicated "representative_service" / "price_service" exist
yet in this codebase to call instead.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB order tests",
)

ORDER_MANAGE = "ORDER_MANAGE"
ORDER_APPROVE = "ORDER_APPROVE"


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


def _user_with_permissions(*permission_codes: str) -> dict[str, str]:
    """Create a fresh user, grant it every permission code given, log in."""

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_order_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"ORDER_TESTER_{suffix}"
        rbac_service.create_role(session, code=role_code, name="Order Tester (test)")
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="order", action="test"
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@pytest.fixture()
def manage_auth_headers() -> dict[str, str]:
    """Holds ORDER_MANAGE only -- proves the plain-lifecycle endpoints work,
    and that approve (ORDER_APPROVE-gated) is separately rejected."""

    return _user_with_permissions(ORDER_MANAGE)


@pytest.fixture()
def approve_auth_headers() -> dict[str, str]:
    """Holds both ORDER_MANAGE and ORDER_APPROVE."""

    return _user_with_permissions(ORDER_MANAGE, ORDER_APPROVE)


@pytest.fixture()
def order_fixtures() -> dict[str, str]:
    """Currency, warehouse, uom, product, price_history, representative,
    customer -- everything ``POST /orders`` needs as FK targets, plus
    enough stock posted to the warehouse to make reservation succeed."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]
        product = Product(
            sku=f"SKU-{suffix}",
            name="Test Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"Test Price List {suffix}",
            price_type="RETAIL",
            currency_id=currency.id,
            owner_scope="GLOBAL",
            is_active=True,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id,
            price_list_id=price_list.id,
            currency_id=currency.id,
            price_type="RETAIL",
            unit_price=decimal.Decimal("100.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history)

        representative = Representative(
            code=f"REP-{suffix}",
            person_name="Test Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-{suffix}",
            name="Order Test Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        from services import inventory_service

        inventory_service.post_transaction(
            session,
            product_id=product.id,
            warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"),
            currency_id=currency.id,
            actor_user_id=system_user.id,
        )

        session.commit()
        return {
            "currency_id": str(currency.id),
            "warehouse_id": str(warehouse.id),
            "product_id": str(product.id),
            "price_history_id": str(price_history.id),
            "representative_id": str(representative.id),
            "customer_id": str(customer.id),
        }
    finally:
        session.close()


def _order_payload(fx: dict[str, str], qty: str = "5") -> dict:
    return {
        "customer_id": fx["customer_id"],
        "representative_id": fx["representative_id"],
        "currency_id": fx["currency_id"],
        "order_type": "LOCAL",
        "fulfillment_mode": "REP_LOCAL",
        "sales_channel": "OFFICE",
        "lines": [
            {
                "product_id": fx["product_id"],
                "fulfillment_warehouse_id": fx["warehouse_id"],
                "price_history_id": fx["price_history_id"],
                "qty_ordered": qty,
                "fulfillment_mode": "REP_LOCAL",
            }
        ],
    }


@requires_database
def test_create_order_starts_in_draft(
    client: TestClient, manage_auth_headers: dict[str, str], order_fixtures: dict[str, str]
) -> None:
    resp = client.post("/api/v1/orders", json=_order_payload(order_fixtures), headers=manage_auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "DRAFT"
    assert body["grand_total"] == "500.0000"
    assert len(body["lines"]) == 1


@requires_database
def test_full_happy_path_to_reserved(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    approve_auth_headers: dict[str, str],
    order_fixtures: dict[str, str],
) -> None:
    order = client.post(
        "/api/v1/orders", json=_order_payload(order_fixtures), headers=manage_auth_headers
    ).json()
    order_id = order["id"]

    submitted = client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth_headers)
    assert submitted.status_code == 200
    assert submitted.json()["state"] == "PENDING_APPROVAL"

    approved = client.post(f"/api/v1/orders/{order_id}/approve", json={}, headers=approve_auth_headers)
    assert approved.status_code == 200
    assert approved.json()["state"] == "APPROVED"

    reserved = client.post(f"/api/v1/orders/{order_id}/reserve", headers=manage_auth_headers)
    assert reserved.status_code == 200, reserved.text
    assert reserved.json()["state"] == "RESERVED"


@requires_database
def test_approve_requires_order_approve_permission(
    client: TestClient, manage_auth_headers: dict[str, str], order_fixtures: dict[str, str]
) -> None:
    order = client.post(
        "/api/v1/orders", json=_order_payload(order_fixtures), headers=manage_auth_headers
    ).json()
    client.post(f"/api/v1/orders/{order['id']}/submit", json={}, headers=manage_auth_headers)

    resp = client.post(f"/api/v1/orders/{order['id']}/approve", json={}, headers=manage_auth_headers)
    assert resp.status_code == 403


@requires_database
def test_reserve_insufficient_stock_backorders(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    approve_auth_headers: dict[str, str],
    order_fixtures: dict[str, str],
) -> None:
    order = client.post(
        "/api/v1/orders",
        json=_order_payload(order_fixtures, qty="1000000"),
        headers=manage_auth_headers,
    ).json()
    order_id = order["id"]
    client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth_headers)
    client.post(f"/api/v1/orders/{order_id}/approve", json={}, headers=approve_auth_headers)

    reserved = client.post(f"/api/v1/orders/{order_id}/reserve", headers=manage_auth_headers)
    assert reserved.status_code == 200
    assert reserved.json()["state"] == "BACKORDERED"


@requires_database
def test_cancel_releases_reservation_and_is_terminal(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    approve_auth_headers: dict[str, str],
    order_fixtures: dict[str, str],
) -> None:
    order = client.post(
        "/api/v1/orders", json=_order_payload(order_fixtures), headers=manage_auth_headers
    ).json()
    order_id = order["id"]
    client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth_headers)
    client.post(f"/api/v1/orders/{order_id}/approve", json={}, headers=approve_auth_headers)
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=manage_auth_headers)

    cancelled = client.post(f"/api/v1/orders/{order_id}/cancel", json={}, headers=manage_auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "CANCELLED"

    again = client.post(f"/api/v1/orders/{order_id}/cancel", json={}, headers=manage_auth_headers)
    assert again.status_code == 409


@requires_database
def test_invalid_transition_returns_409(
    client: TestClient, manage_auth_headers: dict[str, str], order_fixtures: dict[str, str]
) -> None:
    order = client.post(
        "/api/v1/orders", json=_order_payload(order_fixtures), headers=manage_auth_headers
    ).json()
    resp = client.post(f"/api/v1/orders/{order['id']}/start-fulfillment", headers=manage_auth_headers)
    assert resp.status_code == 409


@requires_database
def test_ship_full_order_reaches_shipped(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    approve_auth_headers: dict[str, str],
    order_fixtures: dict[str, str],
) -> None:
    order = client.post(
        "/api/v1/orders", json=_order_payload(order_fixtures), headers=manage_auth_headers
    ).json()
    order_id = order["id"]
    line_id = order["lines"][0]["id"]

    client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth_headers)
    client.post(f"/api/v1/orders/{order_id}/approve", json={}, headers=approve_auth_headers)
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=manage_auth_headers)
    client.post(f"/api/v1/orders/{order_id}/start-fulfillment", headers=manage_auth_headers)

    shipped = client.post(
        f"/api/v1/orders/{order_id}/ship",
        json={"lines": [{"order_line_id": line_id, "quantity": "5"}]},
        headers=manage_auth_headers,
    )
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["state"] == "SHIPPED"
