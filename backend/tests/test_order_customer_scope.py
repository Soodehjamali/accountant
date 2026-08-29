"""Focused tests for customer scope enforcement on Order creation.

Verifies that representative-linked users cannot create Orders for
customers outside their authorized scope (no active assignment).

All tests use real PostgreSQL.
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
    reason="DATABASE_URL is not set; skipping order customer scope tests",
)

ORDER_MANAGE = "ORDER_MANAGE"


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


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant ORDER_MANAGE."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"ord_cust_scope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_OCS_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"OrdCustScope {suffix}",
                             created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=ORDER_MANAGE, name=ORDER_MANAGE,
            resource="order", action="manage", created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code,
                                          permission_code=ORDER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code,
                             assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _setup(client) -> dict:
    """Create two reps, two customers (each assigned to a rep),
    plus all FK targets needed for order creation.

    Rep A -> Customer A (active assignment)
    Rep B -> Customer B (active assignment)
    """
    from datetime import timedelta

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        # Representatives
        rep_a = Representative(
            code=f"REPA-OCS-{suffix}", person_name="Rep A",
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-OCS-{suffix}", person_name="Rep B",
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        # Customers
        cust_a = Customer(
            code=f"CUSTA-OCS-{suffix}", name="Customer A",
            type="CORPORATE", currency_id=currency.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        cust_b = Customer(
            code=f"CUSTB-OCS-{suffix}", name="Customer B",
            type="CORPORATE", currency_id=currency.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([cust_a, cust_b])
        session.flush()

        # Assignments: Cust A -> Rep A, Cust B -> Rep B
        now = datetime.datetime.now(datetime.timezone.utc)
        assign_a = CustomerRepAssignment(
            customer_id=cust_a.id, representative_id=rep_a.id,
            effective_from=now, effective_to=now + timedelta(days=365),
            priority=1, created_by=system_user.id, updated_by=system_user.id,
        )
        assign_b = CustomerRepAssignment(
            customer_id=cust_b.id, representative_id=rep_b.id,
            effective_from=now, effective_to=now + timedelta(days=365),
            priority=1, created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([assign_a, assign_b])
        session.flush()

        # Product + price list for order lines
        product = Product(
            sku=f"SKU-OCS-{suffix}", name="OCS Product",
            base_uom_id=uom.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-OCS-{suffix}", price_type="RETAIL",
            currency_id=currency.id, owner_scope="GLOBAL", is_active=True,
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id, price_list_id=price_list.id,
            currency_id=currency.id, price_type="RETAIL",
            unit_price=decimal.Decimal("50.0000"),
            effective_from=now,
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        # Stock for reservation
        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("25.0000"),
            currency_id=currency.id, actor_user_id=system_user.id,
        )

        # Users
        headers_a, user_a = _create_rep_user(
            session, system_user, rep_a, suffix=f"a_{suffix}")
        headers_b, user_b = _create_rep_user(
            session, system_user, rep_b, suffix=f"b_{suffix}")

        session.commit()
    finally:
        session.close()

    return {
        "headers_a": headers_a,
        "headers_b": headers_b,
        "cust_a_id": str(cust_a.id),
        "cust_b_id": str(cust_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
        "currency_id": str(currency.id),
        "warehouse_id": str(warehouse.id),
        "product_id": str(product.id),
        "price_history_id": str(price_history.id),
        "price_list_id": str(price_list.id),
    }


def _order_payload(fx: dict, customer_id: str, representative_id: str) -> dict:
    return {
        "customer_id": customer_id,
        "representative_id": representative_id,
        "currency_id": fx["currency_id"],
        "price_list_id": fx["price_list_id"],
        "order_type": "LOCAL",
        "fulfillment_mode": "REP_LOCAL",
        "sales_channel": "OFFICE",
        "lines": [{
            "product_id": fx["product_id"],
            "fulfillment_warehouse_id": fx["warehouse_id"],
            "price_history_id": fx["price_history_id"],
            "qty_ordered": "5",
            "fulfillment_mode": "REP_LOCAL",
        }],
    }


@requires_database
class TestOrderCreationCustomerScope:
    """Verify that representative-linked users cannot create orders
    for customers outside their scope."""

    def test_rep_can_create_order_for_own_customer(self, client: TestClient):
        """Representative can create an order for their assigned customer."""
        fx = _setup(client)
        payload = _order_payload(fx, fx["cust_a_id"], fx["rep_a_id"])
        resp = client.post("/api/v1/orders", json=payload, headers=fx["headers_a"])
        assert resp.status_code == 201, resp.text
        assert resp.json()["customer_id"] == fx["cust_a_id"]

    def test_rep_cannot_create_order_for_other_rep_customer(self, client: TestClient):
        """Representative cannot create an order for another rep's customer."""
        fx = _setup(client)
        # Rep A tries to create an order for Customer B (assigned to Rep B)
        payload = _order_payload(fx, fx["cust_b_id"], fx["rep_a_id"])
        resp = client.post("/api/v1/orders", json=payload, headers=fx["headers_a"])
        assert resp.status_code == 404, resp.text

    def test_nonexistent_customer_returns_404(self, client: TestClient):
        """Nonexistent customer returns 404 via scope check (prevents existence leakage)."""
        fx = _setup(client)
        fake_id = str(uuid.uuid4())
        payload = _order_payload(fx, fake_id, fx["rep_a_id"])
        resp = client.post("/api/v1/orders", json=payload, headers=fx["headers_a"])
        # Scope check catches nonexistent customer first -> 404
        assert resp.status_code == 404
