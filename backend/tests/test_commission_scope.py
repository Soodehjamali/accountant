"""Tests proving POST /api/v1/orders/{order_id}/commission order scope enforcement.

Covers:
1. Representative can calculate commission for own order.
2. Representative cannot calculate commission for another representative's order.
3. Admin/staff user retains existing behavior.
4. Out-of-scope attempt creates no commission transaction.

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
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping commission scope tests",
)

COMMISSION_MANAGE = "COMMISSION_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant COMMISSION_MANAGE, return auth headers + user."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"comscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_COMSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ComScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=COMMISSION_MANAGE, name=COMMISSION_MANAGE, resource="commission", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=COMMISSION_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant COMMISSION_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"comscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_COMSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ComScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=COMMISSION_MANAGE, name=COMMISSION_MANAGE, resource="commission", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=COMMISSION_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_completed_order(session, system_user, rep, customer, currency, warehouse,
                            product, price_history):
    """Create a fully completed order for the given representative."""
    from services import invoice_service, order_service

    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            order_service.OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=decimal.Decimal("3"),
                fulfillment_mode="REP_LOCAL",
            )
        ],
        created_by=system_user.id,
    )
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)

    try:
        rbac_service.create_permission(
            session, code="ORDER_APPROVE", name="Approve", resource="order", action="approve",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass

    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

    order_line = list(order_service.list_order_lines(session, order.id))[0]
    order_service.ship_order(
        session, order.id,
        shipments=[order_service.ShipmentInput(order_line_id=order_line.id, quantity=decimal.Decimal("3"))],
        actor_user_id=system_user.id,
    )

    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    invoice_service.issue_invoice(session, invoice.id, actor_user_id=system_user.id)
    invoice_service.record_payment(
        session, invoice.id, amount=decimal.Decimal("300.0000"), actor_user_id=system_user.id,
    )
    session.refresh(invoice)
    assert invoice.state == "PAID"

    order_service.mark_paid(session, order.id, actor_user_id=system_user.id)
    order_service.mark_completed(session, order.id, actor_user_id=system_user.id)
    session.refresh(order)
    assert order.state == "COMPLETED"
    return order


def _setup(client: TestClient):
    """Create two representatives each with a completed order, a global commission config,
    plus two rep-linked users and one admin."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        rep_a = Representative(
            code=f"REPA-CS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-CS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-CS-{suffix}", name="CommissionScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-CS-{suffix}", price_type="RETAIL", currency_id=currency.id,
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

        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"), currency_id=currency.id, actor_user_id=system_user.id,
        )
        session.flush()

        customer_a = Customer(
            code=f"CUSTA-CS-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-CS-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Create completed orders for each representative
        order_a = _create_completed_order(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        order_b = _create_completed_order(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )

        # Create users
        headers_a, user_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        headers_b, user_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

    # Create a global commission config via admin user
    resp = client.post(
        "/api/v1/commission-configs",
        json={
            "rate": "10.0000",
            "effective_from": "2025-01-01T00:00:00Z",
            "order_type": "LOCAL",
        },
        headers=headers_admin,
    )
    assert resp.status_code == 201, resp.text

    return {
        "headers_a": headers_a,
        "headers_b": headers_b,
        "headers_admin": headers_admin,
        "order_a_id": str(order_a.id),
        "order_b_id": str(order_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestCommissionOrderScope:
    """POST /orders/{order_id}/commission order scope enforcement."""

    def test_representative_can_commission_own_order(self, client: TestClient):
        """Representative can calculate commission for their own completed order."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        txn = resp.json()
        assert txn["signed_amount"] == "30.0000"  # 10% of 300
        assert txn["rate_applied"] == "10.0000"

    def test_representative_cannot_commission_other_rep_order(self, client: TestClient):
        """Representative cannot calculate commission for another rep's order — 404."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_b_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_commission_any_order(self, client: TestClient):
        """Admin/staff user can calculate commission for any order."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_b_id']}/commission",
            json={},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 201, resp.text
        txn = resp.json()
        assert txn["signed_amount"] == "30.0000"  # 10% of 300

    def test_out_of_scope_creates_no_commission(self, client: TestClient):
        """Out-of-scope attempt does not create any commission transaction."""
        data = _setup(client)
        # Count transactions before
        from database.models.commission_transaction import CommissionTransaction

        session = get_session_factory()()
        try:
            before = len(session.execute(
                __import__("sqlalchemy").select(CommissionTransaction).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_b_id"])
                )
            ).scalars().all())
        finally:
            session.close()

        resp = client.post(
            f"/api/v1/orders/{data['order_b_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no new commission transaction was created
        session = get_session_factory()()
        try:
            after = len(session.execute(
                __import__("sqlalchemy").select(CommissionTransaction).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_b_id"])
                )
            ).scalars().all())
            assert after == before, "No commission transaction should have been created for out-of-scope order"
        finally:
            session.close()
