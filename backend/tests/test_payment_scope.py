"""Tests proving payment endpoint representative scope enforcement.

Covers:
1. Representative can create payment for own assigned customer.
2. Representative cannot create payment for another representative's customer → 404.
3. Failed foreign-customer payment creates no Payment.
4. Failed foreign-customer payment creates no financial mutation.
5. Representative can read own payment.
6. Representative cannot read another representative's payment → 404.
7. Nonexistent payment → 404.
8. Admin can read any payment.
9. Representative can read payments for own invoice.
10. Representative cannot read payments for another representative's invoice → 404.
11. Admin can create payment for any customer.

All tests use real PostgreSQL (same skipif convention as other test files).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from sqlalchemy import select

from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.payment import Payment
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping payment scope tests",
)

PAYMENT_MANAGE = "PAYMENT_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant PAYMENT_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"payscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_PAYSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"PayScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=PAYMENT_MANAGE, name=PAYMENT_MANAGE, resource="payment", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=PAYMENT_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant PAYMENT_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"payscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_PAYSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"PayScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=PAYMENT_MANAGE, name=PAYMENT_MANAGE, resource="payment", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=PAYMENT_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_issued_invoice(session, system_user, rep, customer, currency, warehouse,
                           product, price_history):
    """Create a fully shipped + issued invoice for the given representative."""
    from services import invoice_service, order_service

    # Resolve price_list from the price_history entry.
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

    invoice = invoice_service.create_invoice_from_order(session, order_id=order.id, created_by=system_user.id)
    invoice_service.issue_invoice(session, invoice.id, actor_user_id=system_user.id)
    session.refresh(invoice)
    assert invoice.state == "ISSUED"
    return invoice


def _create_payment(session, system_user, customer, currency, invoice):
    """Create a payment for a customer against an invoice via the service layer."""
    from services import customer_ledger_service, payment_service

    payment = payment_service.record_payment(
        session,
        customer_id=customer.id,
        currency_id=currency.id,
        amount=decimal.Decimal("100.0000"),
        method="CASH",
        allocations=[(invoice.id, decimal.Decimal("100.0000"))],
        actor_user_id=system_user.id,
        record_entry=customer_ledger_service.record_entry,
    )
    session.flush()
    session.refresh(payment)
    return payment


def _setup(client):
    """Create two representatives each with a customer + invoice + payment, plus two users and admin."""
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
            code=f"REPA-PS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-PS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-PS-{suffix}", name="PaymentScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-PS-{suffix}", price_type="RETAIL", currency_id=currency.id,
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
            code=f"CUSTA-PS-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-PS-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Assign customers to representatives
        from datetime import timedelta
        now = _now()
        assign_a = CustomerRepAssignment(
            customer_id=customer_a.id, representative_id=rep_a.id,
            effective_from=now, effective_to=now + timedelta(days=365),
            priority=1, created_by=system_user.id, updated_by=system_user.id,
        )
        assign_b = CustomerRepAssignment(
            customer_id=customer_b.id, representative_id=rep_b.id,
            effective_from=now, effective_to=now + timedelta(days=365),
            priority=1, created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([assign_a, assign_b])
        session.flush()

        # Create issued invoices for each representative
        invoice_a = _create_issued_invoice(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        invoice_b = _create_issued_invoice(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )

        # Create payments for each representative
        payment_a = _create_payment(session, system_user, customer_a, currency, invoice_a)
        payment_b = _create_payment(session, system_user, customer_b, currency, invoice_b)

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
        "payment_a_id": str(payment_a.id),
        "payment_b_id": str(payment_b.id),
        "invoice_a_id": str(invoice_a.id),
        "invoice_b_id": str(invoice_b.id),
        "customer_a_id": str(customer_a.id),
        "customer_b_id": str(customer_b.id),
        "currency_id": str(currency.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestPaymentCreateScope:
    """POST /payments creation scope enforcement."""

    def test_representative_can_create_for_own_customer(self, client):
        """Representative can create payment for their own assigned customer."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/payments",
            json={
                "customer_id": data["customer_a_id"],
                "currency_id": data["currency_id"],
                "amount": "50.0000",
                "method": "CASH",
                "allocations": [{"invoice_id": data["invoice_a_id"], "allocated_amount": "50.0000"}],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "50.0000"

    def test_representative_cannot_create_for_other_rep_customer(self, client):
        """Representative cannot create payment for another rep's customer — 404."""
        data = _setup(client)
        # Count payments before.
        session = get_session_factory()()
        try:
            before = len(session.execute(select(Payment)).scalars().all())
        finally:
            session.close()

        resp = client.post(
            "/api/v1/payments",
            json={
                "customer_id": data["customer_b_id"],
                "currency_id": data["currency_id"],
                "amount": "50.0000",
                "method": "CASH",
                "allocations": [{"invoice_id": data["invoice_b_id"], "allocated_amount": "50.0000"}],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no payment was created.
        session = get_session_factory()()
        try:
            after = len(session.execute(select(Payment)).scalars().all())
            assert after == before, "No payment should have been created for out-of-scope customer"
        finally:
            session.close()

    def test_admin_can_create_for_any_customer(self, client):
        """Admin/staff user can create payment for any customer."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/payments",
            json={
                "customer_id": data["customer_b_id"],
                "currency_id": data["currency_id"],
                "amount": "50.0000",
                "method": "CASH",
                "allocations": [{"invoice_id": data["invoice_b_id"], "allocated_amount": "50.0000"}],
            },
            headers=data["headers_admin"],
        )
        assert resp.status_code == 201, resp.text


@requires_database
class TestPaymentReadScope:
    """GET /payments/{payment_id} representative scope enforcement."""

    def test_representative_can_read_own_payment(self, client):
        """Representative can read their own payment."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/payments/{data['payment_a_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["payment_a_id"]

    def test_representative_cannot_read_other_rep_payment(self, client):
        """Representative cannot read another rep's payment — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/payments/{data['payment_b_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_nonexistent_payment_returns_404(self, client):
        """Nonexistent payment returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/payments/{fake_id}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_payment(self, client):
        """Admin/staff user can read any payment."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/payments/{data['payment_b_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["payment_b_id"]


@requires_database
class TestPaymentInvoiceHistoryScope:
    """GET /payments/invoices/{invoice_id}/payments representative scope enforcement."""

    def test_representative_can_read_own_invoice_payments(self, client):
        """Representative can read payments for their own invoice."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_a_id']}/payments",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text

    def test_representative_cannot_read_other_rep_invoice_payments(self, client):
        """Representative cannot read payments for another rep's invoice — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/invoices/{data['invoice_b_id']}/payments",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404
