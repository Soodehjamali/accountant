"""Tests proving GET /api/v1/orders representative scope is enforced.

Covers:
1. Representative sees only own orders when representative_id omitted.
2. Representative cannot bypass scope by supplying another rep's UUID.
3. Admin/staff user retains unscoped list behavior.

All tests use real PostgreSQL (same skipif convention as other test files).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping order list scope tests",
)

ORDER_MANAGE = "ORDER_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str) -> dict:
    """Create a user linked to a representative, grant ORDER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"listscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session,
        username=username,
        email=f"{username}@example.invalid",
        password=password,
        created_by=system_user.id,
    )
    # Link user to representative
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_LISTSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ListScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=ORDER_MANAGE, name=ORDER_MANAGE, resource="order", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=ORDER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id),
        secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str) -> dict:
    """Create an admin user (no representative link), grant ORDER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"listscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session,
        username=username,
        email=f"{username}@example.invalid",
        password=password,
        created_by=system_user.id,
    )
    # No representative_id set — admin/staff user
    session.flush()

    role_code = f"ROLE_LISTSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ListScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=ORDER_MANAGE, name=ORDER_MANAGE, resource="order", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=ORDER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id),
        secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _setup(client: TestClient):
    """Create two representatives, two orders, return headers + order IDs."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        # Two representatives
        rep_a = Representative(
            code=f"REPA-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        # Product + price
        product = Product(
            sku=f"SKU-LS-{suffix}", name="ListScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-LS-{suffix}", price_type="RETAIL", currency_id=currency.id,
            owner_scope="GLOBAL", is_active=True, created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id, price_list_id=price_list.id, currency_id=currency.id,
            price_type="RETAIL", unit_price=decimal.Decimal("100.0000"), effective_from=_now(),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        # Stock
        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"), currency_id=currency.id, actor_user_id=system_user.id,
        )
        session.flush()

        # Customers for each rep
        customer_a = Customer(
            code=f"CUSTA-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Two users: one per representative
        user_a_headers, _ = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        user_b_headers, _ = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        admin_headers, _ = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

    # Now create orders via API (one per representative)
    order_a = client.post(
        "/api/v1/orders",
        json={
            "customer_id": str(customer_a.id),
            "representative_id": str(rep_a.id),
            "currency_id": str(currency.id),
            "order_type": "LOCAL",
            "fulfillment_mode": "REP_LOCAL",
            "sales_channel": "OFFICE",
            "lines": [{
                "product_id": str(product.id),
                "fulfillment_warehouse_id": str(warehouse.id),
                "price_history_id": str(price_history.id),
                "qty_ordered": "3",
                "fulfillment_mode": "REP_LOCAL",
            }],
        },
        headers=user_a_headers,
    )
    assert order_a.status_code == 201, order_a.text

    order_b = client.post(
        "/api/v1/orders",
        json={
            "customer_id": str(customer_b.id),
            "representative_id": str(rep_b.id),
            "currency_id": str(currency.id),
            "order_type": "LOCAL",
            "fulfillment_mode": "REP_LOCAL",
            "sales_channel": "OFFICE",
            "lines": [{
                "product_id": str(product.id),
                "fulfillment_warehouse_id": str(warehouse.id),
                "price_history_id": str(price_history.id),
                "qty_ordered": "2",
                "fulfillment_mode": "REP_LOCAL",
            }],
        },
        headers=user_b_headers,
    )
    assert order_b.status_code == 201, order_b.text

    return {
        "headers_a": user_a_headers,
        "headers_b": user_b_headers,
        "headers_admin": admin_headers,
        "order_a_id": order_a.json()["id"],
        "order_b_id": order_b.json()["id"],
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestOrderListScope:
    """GET /orders representative scope enforcement."""

    def test_representative_sees_only_own_orders(self, client: TestClient):
        """When representative_id is omitted, rep sees only their own orders."""
        data = _setup(client)
        resp = client.get("/api/v1/orders", headers=data["headers_a"])
        assert resp.status_code == 200
        items = resp.json()["items"]
        order_ids = [o["id"] for o in items]
        assert data["order_a_id"] in order_ids
        assert data["order_b_id"] not in order_ids

    def test_representative_cannot_bypass_with_other_rep_id(self, client: TestClient):
        """Supplying another representative's UUID does not bypass scope."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/orders?representative_id={data['rep_b_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        order_ids = [o["id"] for o in items]
        # Must still see ONLY rep_a's orders, not rep_b's
        assert data["order_a_id"] in order_ids
        assert data["order_b_id"] not in order_ids

    def test_own_representative_id_returns_own_orders(self, client: TestClient):
        """Supplying own representative_id still works."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/orders?representative_id={data['rep_a_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        order_ids = [o["id"] for o in items]
        assert data["order_a_id"] in order_ids
        assert data["order_b_id"] not in order_ids

    def test_admin_sees_all_orders(self, client: TestClient):
        """Admin/staff user (no representative) sees all orders."""
        data = _setup(client)
        resp = client.get("/api/v1/orders", headers=data["headers_admin"])
        assert resp.status_code == 200
        items = resp.json()["items"]
        order_ids = [o["id"] for o in items]
        assert data["order_a_id"] in order_ids
        assert data["order_b_id"] in order_ids

    def test_admin_can_filter_by_representative(self, client: TestClient):
        """Admin can optionally filter by representative_id."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/orders?representative_id={data['rep_a_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        order_ids = [o["id"] for o in items]
        assert data["order_a_id"] in order_ids
        assert data["order_b_id"] not in order_ids
