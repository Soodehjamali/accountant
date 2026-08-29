"""Tests proving POST /api/v1/invoices/from-order order scope enforcement.

Covers:
1. Representative can create invoice from own order.
2. Representative cannot create invoice from another representative's order.
3. Out-of-scope attempt creates no side effects.
4. Admin/staff user retains existing behavior.

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
    reason="DATABASE_URL is not set; skipping invoice scope tests",
)

INVOICE_MANAGE = "INVOICE_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


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
    """Create a user linked to a representative, grant INVOICE_MANAGE + ORDER_MANAGE."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"invscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_INVSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"InvScope {suffix}", created_by=system_user.id)
    for code in (INVOICE_MANAGE, "ORDER_MANAGE"):
        try:
            rbac_service.create_permission(
                session, code=code, name=code, resource="order", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant INVOICE_MANAGE + ORDER_MANAGE."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"invscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_INVSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"InvScopeAdmin {suffix}", created_by=system_user.id)
    for code in (INVOICE_MANAGE, "ORDER_MANAGE", "ORDER_APPROVE"):
        try:
            rbac_service.create_permission(
                session, code=code, name=code, resource="order", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_shipped_order(session, system_user, rep, customer, currency, warehouse,
                          product, price_history):
    """Create a fully shipped order for the given representative."""
    from services import order_service

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
    session.flush()
    session.refresh(order)
    assert order.state == "SHIPPED"
    return order


def _setup(client: TestClient):
    """Create two representatives each with a shipped order, plus two users + admin."""
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
            code=f"REPA-IS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-IS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-IS-{suffix}", name="InvScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-IS-{suffix}", price_type="RETAIL", currency_id=currency.id,
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
            code=f"CUSTA-IS-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-IS-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Create shipped orders for each representative
        order_a = _create_shipped_order(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        order_b = _create_shipped_order(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )

        # Create users
        headers_a, user_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        headers_b, user_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

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
class TestInvoiceFromOrderScope:
    """POST /invoices/from-order order scope enforcement."""

    def test_representative_can_invoice_own_order(self, client: TestClient):
        """Representative creates invoice from their own order — succeeds."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/invoices/from-order",
            json={"order_id": data["order_a_id"]},
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["state"] == "DRAFT"

    def test_representative_cannot_invoice_other_rep_order(self, client: TestClient):
        """Representative cannot create invoice from another rep's order — returns 404."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/invoices/from-order",
            json={"order_id": data["order_b_id"]},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_creates_no_invoice(self, client: TestClient):
        """Out-of-scope attempt does not create any invoice."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/invoices/from-order",
            json={"order_id": data["order_b_id"]},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404
        # Verify no invoice was created for order_b
        from database.models.invoice import Invoice
        from database.models.invoice_order import InvoiceOrder

        session = get_session_factory()()
        try:
            inv_orders = session.execute(
                __import__("sqlalchemy").select(InvoiceOrder).where(
                    InvoiceOrder.order_id == uuid.UUID(data["order_b_id"])
                )
            ).scalars().all()
            assert len(inv_orders) == 0, "No invoice should have been created for out-of-scope order"
        finally:
            session.close()

    def test_admin_can_invoice_any_order(self, client: TestClient):
        """Admin/staff user can create invoice from any order."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/invoices/from-order",
            json={"order_id": data["order_b_id"]},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["state"] == "DRAFT"
