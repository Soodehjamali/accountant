"""Focused tests for Order ↔ Invoice ↔ Payment integration.

Covers:
1. POST /orders/{id}/invoice creates a real ISSUED invoice and transitions
   order to INVOICED.
2. Invoice lines are created from order lines.
3. Customer ledger entry is posted on invoice issuance.
4. POST /orders/{id}/pay records a real payment and transitions both
   invoice and order to PAID.
5. Payment ledger entry is posted.
6. POST /orders/{id}/pay without an existing invoice returns 409.
7. Invoice amount_paid / balance_due are updated correctly.
8. Order invoiced_at / paid_at timestamps are set.

All tests use real PostgreSQL.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models.customer import Customer
from database.models.customer_ledger import CustomerLedger
from database.models.customer_ledger_entry import CustomerLedgerEntry
from database.models.invoice import Invoice
from database.models.invoice_line import InvoiceLine
from database.models.invoice_order import InvoiceOrder
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, order_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping order-invoice integration tests",
)

ORDER_MANAGE = "ORDER_MANAGE"
INVOICE_MANAGE = "INVOICE_MANAGE"


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


def _make_admin_user(suffix: str) -> dict[str, str]:
    """Create an admin user with ORDER_MANAGE + INVOICE_MANAGE."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        username = f"ordinv_{suffix}"
        password = "correct-horse-battery-staple"
        user = auth_service.create_user(
            session, username=username, email=f"{username}@example.invalid",
            password=password, created_by=system_user.id,
        )
        role_code = f"ROLE_OI_{suffix}"
        rbac_service.create_role(session, code=role_code, name=f"OrdInv {suffix}",
                                 created_by=system_user.id)
        for code in (ORDER_MANAGE, INVOICE_MANAGE, "ORDER_APPROVE"):
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="order", action="manage",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(session, role_code=role_code,
                                                  permission_code=code)
        rbac_service.assign_role(session, user_id=user.id, role_code=role_code,
                                 assigned_by=system_user.id)
        session.commit()
    finally:
        session.close()
    return _login(username, password)


def _setup() -> dict:
    """Create all FK targets and a shipped order for testing."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        rep = Representative(
            code=f"REP-OI-{suffix}", person_name="Integration Rep",
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(rep)
        session.flush()

        customer = Customer(
            code=f"CUST-OI-{suffix}", name="Integration Customer",
            type="CORPORATE", currency_id=currency.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        product = Product(
            sku=f"SKU-OI-{suffix}", name="Integration Product",
            base_uom_id=uom.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-OI-{suffix}", price_type="RETAIL", currency_id=currency.id,
            owner_scope="GLOBAL", is_active=True,
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id, price_list_id=price_list.id,
            currency_id=currency.id, price_type="RETAIL",
            unit_price=decimal.Decimal("200.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        # Stock
        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("100.0000"),
            currency_id=currency.id, actor_user_id=system_user.id,
        )

        # Create a shipped order (3 units @ 200 = 600)
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
        order_service.approve_order(session, order.id, actor_user_id=system_user.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
        order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)
        order_line = list(order_service.list_order_lines(session, order.id))[0]
        order_service.ship_order(
            session, order.id,
            shipments=[order_service.ShipmentInput(
                order_line_id=order_line.id, quantity=decimal.Decimal("3")
            )],
            actor_user_id=system_user.id,
        )
        session.flush()
        session.refresh(order)
        assert order.state == "SHIPPED"

        session.commit()
        return {
            "order_id": str(order.id),
            "customer_id": str(customer.id),
            "currency_id": str(currency.id),
            "grand_total": str(order.grand_total),
        }
    finally:
        session.close()


@requires_database
class TestOrderInvoiceIntegration:
    """POST /orders/{id}/invoice creates a real invoice."""

    def test_creates_issued_invoice(self, client: TestClient):
        fx = _setup()
        headers = _make_admin_user(uuid.uuid4().hex[:8])

        resp = client.post(
            f"/api/v1/orders/{fx['order_id']}/invoice",
            json={"note": "Test invoice"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Order should now be INVOICED
        assert body["state"] == "INVOICED"

        # Verify invoice was created
        session = get_session_factory()()
        try:
            invoice_link = session.execute(
                select(InvoiceOrder).where(
                    InvoiceOrder.order_id == uuid.UUID(fx["order_id"])
                )
            ).scalar_one_or_none()
            assert invoice_link is not None

            invoice = session.get(Invoice, invoice_link.invoice_id)
            assert invoice is not None
            assert invoice.state == "ISSUED"
            assert invoice.grand_total == decimal.Decimal(fx["grand_total"])

            # Verify invoice lines exist
            lines = session.execute(
                select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
            ).scalars().all()
            assert len(lines) == 1
            assert lines[0].qty == decimal.Decimal("3")
            assert lines[0].unit_price == decimal.Decimal("200.0000")
        finally:
            session.close()

    def test_customer_ledger_entry_posted(self, client: TestClient):
        fx = _setup()
        headers = _make_admin_user(uuid.uuid4().hex[:8])

        client.post(
            f"/api/v1/orders/{fx['order_id']}/invoice",
            json={},
            headers=headers,
        )

        # Verify customer ledger entry
        session = get_session_factory()()
        try:
            customer_id = uuid.UUID(fx["customer_id"])
            ledger = session.execute(
                select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
            ).scalar_one_or_none()
            assert ledger is not None

            entry = session.execute(
                select(CustomerLedgerEntry).where(
                    CustomerLedgerEntry.customer_ledger_id == ledger.id,
                    CustomerLedgerEntry.entry_type == "INVOICE_ISSUED",
                )
            ).scalar_one_or_none()
            assert entry is not None
            assert entry.signed_amount == decimal.Decimal(fx["grand_total"])
        finally:
            session.close()


@requires_database
class TestOrderPaymentIntegration:
    """POST /orders/{id}/pay records a real payment."""

    def _create_invoice_first(self, client, fx, headers):
        """Helper: create an invoice first, return invoice_id."""
        resp = client.post(
            f"/api/v1/orders/{fx['order_id']}/invoice",
            json={},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        session = get_session_factory()()
        try:
            link = session.execute(
                select(InvoiceOrder).where(
                    InvoiceOrder.order_id == uuid.UUID(fx["order_id"])
                )
            ).scalar_one_or_none()
            return str(link.invoice_id)
        finally:
            session.close()

    def test_pay_transitions_to_paid(self, client: TestClient):
        fx = _setup()
        headers = _make_admin_user(uuid.uuid4().hex[:8])

        # Create invoice first
        invoice_id = self._create_invoice_first(client, fx, headers)

        # Pay full amount
        resp = client.post(
            f"/api/v1/orders/{fx['order_id']}/pay",
            json={
                "amount": fx["grand_total"],
                "method": "CASH",
                "reference": "TEST-REF-001",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "PAID"
        assert resp.json()["paid_at"] is not None

        # Verify invoice is PAID
        session = get_session_factory()()
        try:
            invoice = session.get(Invoice, uuid.UUID(invoice_id))
            assert invoice.state == "PAID"
            assert invoice.amount_paid == decimal.Decimal(fx["grand_total"])
            assert invoice.balance_due == decimal.Decimal("0")
        finally:
            session.close()

    def test_payment_ledger_entry_posted(self, client: TestClient):
        fx = _setup()
        headers = _make_admin_user(uuid.uuid4().hex[:8])

        self._create_invoice_first(client, fx, headers)

        client.post(
            f"/api/v1/orders/{fx['order_id']}/pay",
            json={"amount": fx["grand_total"], "method": "BANK_TRANSFER"},
            headers=headers,
        )

        # Verify customer ledger has PAYMENT_RECEIVED entry
        session = get_session_factory()()
        try:
            customer_id = uuid.UUID(fx["customer_id"])
            ledger = session.execute(
                select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
            ).scalar_one_or_none()
            assert ledger is not None

            payment_entry = session.execute(
                select(CustomerLedgerEntry).where(
                    CustomerLedgerEntry.customer_ledger_id == ledger.id,
                    CustomerLedgerEntry.entry_type == "PAYMENT_RECEIVED",
                )
            ).scalar_one_or_none()
            assert payment_entry is not None
            # Payment is a credit (negative amount)
            assert payment_entry.signed_amount == -decimal.Decimal(fx["grand_total"])
        finally:
            session.close()

    def test_pay_without_invoice_returns_409(self, client: TestClient):
        fx = _setup()
        headers = _make_admin_user(uuid.uuid4().hex[:8])

        # Try to pay without creating an invoice first
        resp = client.post(
            f"/api/v1/orders/{fx['order_id']}/pay",
            json={"amount": fx["grand_total"], "method": "CASH"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert "No invoice exists" in resp.json()["detail"]

    def test_order_not_shipped_for_invoice_returns_error(self, client: TestClient):
        """Creating an invoice for a non-SHIPPED order fails."""
        # Create a DRAFT order (not shipped)
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
            uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
            bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            rep = Representative(
                code=f"REP-DRAFT-{suffix}", person_name="Draft Rep",
                status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep)
            session.flush()

            customer = Customer(
                code=f"CUST-DRAFT-{suffix}", name="Draft Customer",
                type="CORPORATE", currency_id=currency.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(customer)
            session.flush()

            product = Product(
                sku=f"SKU-DRAFT-{suffix}", name="Draft Product",
                base_uom_id=uom.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(product)
            session.flush()

            price_list = PriceList(
                name=f"PL-DRAFT-{suffix}", price_type="RETAIL", currency_id=currency.id,
                owner_scope="GLOBAL", is_active=True,
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(price_list)
            session.flush()

            price_history = PriceHistory(
                product_id=product.id, price_list_id=price_list.id,
                currency_id=currency.id, price_type="RETAIL",
                unit_price=decimal.Decimal("100.0000"),
                effective_from=datetime.datetime.now(datetime.timezone.utc),
                created_by=system_user.id,
            )
            session.add(price_history)
            session.flush()

            from services import inventory_service
            inventory_service.post_transaction(
                session, product_id=product.id, warehouse_id=warehouse.id,
                movement_type_code="INITIAL_OPENING_BALANCE",
                signed_quantity=decimal.Decimal("100"),
                unit_cost=decimal.Decimal("50.0000"),
                currency_id=currency.id, actor_user_id=system_user.id,
            )

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
                        qty_ordered=decimal.Decimal("1"),
                        fulfillment_mode="REP_LOCAL",
                    )
                ],
                created_by=system_user.id,
            )
            session.commit()
            order_id = str(order.id)
        finally:
            session.close()

        headers = _make_admin_user(uuid.uuid4().hex[:8])
        resp = client.post(
            f"/api/v1/orders/{order_id}/invoice",
            json={},
            headers=headers,
        )
        # Should fail: order is DRAFT, not SHIPPED
        assert resp.status_code == 422
