"""Tests proving invoice-by-id endpoint representative scope enforcement.

Covers:
1. Representative can read own invoice.
2. Representative cannot read another representative's invoice → 404.
3. Representative cannot mutate another representative's invoice → 404.
4. Admin/staff user retains unrestricted access.
5. Nonexistent invoice → 404 (same as out-of-scope).
6. Out-of-scope write attempt creates no side effects.

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
from database.models.invoice import Invoice
from database.models.invoice_order import InvoiceOrder
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


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant INVOICE_MANAGE, return auth headers + user."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"invscope2_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_INVSCOPE2_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"InvScope2 {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=INVOICE_MANAGE, name=INVOICE_MANAGE, resource="invoice", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=INVOICE_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant INVOICE_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"invscope2_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_INVSCOPE2_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"InvScope2Admin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=INVOICE_MANAGE, name=INVOICE_MANAGE, resource="invoice", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=INVOICE_MANAGE)
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

    price_list = session.get(PriceList, price_history.price_list_id)

    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        price_list_id=price_list.id,
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


def _create_invoice(session, system_user, order):
    """Create a draft invoice from a shipped order via the service layer."""
    from services import invoice_service

    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    session.flush()
    session.refresh(invoice)
    return invoice


def _setup(client: TestClient):
    """Create two representatives each with a shipped order + invoice, plus two rep-linked users and one admin."""
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
            code=f"REPA-IS2-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-IS2-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-IS2-{suffix}", name="InvoiceScope2 Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-IS2-{suffix}", price_type="RETAIL", currency_id=currency.id,
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
            code=f"CUSTA-IS2-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-IS2-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Create shipped orders + invoices for each representative
        order_a = _create_shipped_order(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        order_b = _create_shipped_order(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )
        invoice_a = _create_invoice(session, system_user, order_a)
        invoice_b = _create_invoice(session, system_user, order_b)

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
        "invoice_a_id": str(invoice_a.id),
        "invoice_b_id": str(invoice_b.id),
        "order_a_id": str(order_a.id),
        "order_b_id": str(order_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestInvoiceReadScope:
    """GET /invoices/{invoice_id} representative scope enforcement."""

    def test_representative_can_read_own_invoice(self, client: TestClient):
        """Representative can read their own invoice."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_a_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["invoice_a_id"]

    def test_representative_cannot_read_other_rep_invoice(self, client: TestClient):
        """Representative cannot read another rep's invoice — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_b_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_representative_cannot_read_other_rep_invoice_lines(self, client: TestClient):
        """Representative cannot read another rep's invoice lines — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_b_id']}/lines",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_representative_cannot_read_other_rep_invoice_history(self, client: TestClient):
        """Representative cannot read another rep's invoice history — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_b_id']}/history",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_nonexistent_invoice_returns_404(self, client: TestClient):
        """Nonexistent invoice returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/invoices/{fake_id}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_invoice(self, client: TestClient):
        """Admin/staff user can read any invoice."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_b_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["invoice_b_id"]


@requires_database
class TestInvoiceWriteScope:
    """POST /invoices/{invoice_id}/* write endpoint scope enforcement."""

    def test_representative_cannot_issue_other_rep_invoice(self, client: TestClient):
        """Representative cannot issue another rep's invoice — 404, no state change."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/issue",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404
        # Verify invoice is still DRAFT (no state change).
        session = get_session_factory()()
        try:
            invoice = session.get(Invoice, uuid.UUID(data["invoice_b_id"]))
            assert invoice.state == "DRAFT"
        finally:
            session.close()

    def test_representative_cannot_pay_other_rep_invoice(self, client: TestClient):
        """Representative cannot pay another rep's invoice — 404, no payment recorded."""
        data = _setup(client)
        # First issue invoice_b as admin so it can accept payments.
        admin_resp = client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/issue",
            json={},
            headers=data["headers_admin"],
        )
        assert admin_resp.status_code == 200, admin_resp.text

        # Count payments before.
        from database.models.payment import Payment
        session = get_session_factory()()
        try:
            before_payments = len(session.execute(
                __import__("sqlalchemy").select(Payment)
            ).scalars().all())
        finally:
            session.close()

        # Attempt pay as rep_a (out of scope).
        resp = client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/pay",
            json={"amount": "100.0000"},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no new payment was created.
        session = get_session_factory()()
        try:
            after_payments = len(session.execute(
                __import__("sqlalchemy").select(Payment)
            ).scalars().all())
            assert after_payments == before_payments, "No payment should have been created for out-of-scope invoice"
        finally:
            session.close()

    def test_representative_cannot_void_other_rep_invoice(self, client: TestClient):
        """Representative cannot void another rep's invoice — 404, no state change."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/void",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404
        # Verify invoice is still DRAFT.
        session = get_session_factory()()
        try:
            invoice = session.get(Invoice, uuid.UUID(data["invoice_b_id"]))
            assert invoice.state == "DRAFT"
        finally:
            session.close()

    def test_admin_can_issue_any_invoice(self, client: TestClient):
        """Admin/staff user can issue any invoice."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/issue",
            json={},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "ISSUED"

    def test_admin_can_pay_any_invoice(self, client: TestClient):
        """Admin/staff user can pay any invoice."""
        data = _setup(client)
        # Issue first.
        client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/issue",
            json={},
            headers=data["headers_admin"],
        )
        resp = client.post(
            f"/api/v1/invoices/{data['invoice_b_id']}/pay",
            json={"amount": "100.0000"},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] in ("PARTIALLY_PAID", "PAID")
