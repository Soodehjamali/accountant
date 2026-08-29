"""Tests for the payment endpoints and service.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_invoices.py``).  Builds its
own supporting rows directly via the ORM/service layer.

Test matrix:
* Full payment: pay an entire invoice -> PAID
* Split payment: pay two invoices from one payment
* Over-allocation of payment amount rejected (422)
* Over-allocation of invoice balance rejected (422)
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
    reason="DATABASE_URL is not set; skipping live DB payment tests",
)

PAYMENT_MANAGE = "PAYMENT_MANAGE"


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
        username = f"test_pay_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"PAYMENT_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Payment Tester (test)", created_by=system_user.id
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session,
                    code=code,
                    name=code,
                    resource="payment",
                    action="test",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=code
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@pytest.fixture()
def manage_auth_headers() -> dict[str, str]:
    return _user_with_permissions(PAYMENT_MANAGE)


@pytest.fixture()
def payment_fixtures() -> dict:
    """All supporting rows plus two ISSUED invoices (grand_total=500 each)."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]
        product = Product(
            sku=f"SKU-PAY-{suffix}",
            name="Payment Test Product",
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
            code=f"REP-PAY-{suffix}",
            person_name="Payment Test Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-PAY-{suffix}",
            name="Payment Test Customer",
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

        # Helper to create and ship an order, then issue its invoice.
        from services import invoice_service, order_service

        def _make_issued_invoice(label: str) -> str:
            order = order_service.create_order(
                session,
                customer_id=customer.id,
                representative_id=representative.id,
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
                        qty_ordered=decimal.Decimal("5"),
                        fulfillment_mode="REP_LOCAL",
                    )
                ],
                created_by=system_user.id,
            )
            order_service.submit_order(session, order.id, actor_user_id=system_user.id)

            try:
                rbac_service.create_permission(
                    session,
                    code="ORDER_APPROVE",
                    name="Approve orders",
                    resource="order",
                    action="approve",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass

            order_service.approve_order(session, order.id, actor_user_id=system_user.id)
            order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
            order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

            order_line = list(order_service.list_order_lines(session, order.id))[0]
            order_service.ship_order(
                session,
                order.id,
                shipments=[
                    order_service.ShipmentInput(
                        order_line_id=order_line.id, quantity=decimal.Decimal("5")
                    )
                ],
                actor_user_id=system_user.id,
            )

            invoice = invoice_service.create_invoice_from_order(
                session, order_id=order.id, created_by=system_user.id
            )
            invoice_service.issue_invoice(
                session, invoice.id, actor_user_id=system_user.id
            )
            session.refresh(invoice)
            assert invoice.state == "ISSUED", f"Invoice state is {invoice.state}"
            return str(invoice.id)

        invoice_id_1 = _make_issued_invoice("first")
        invoice_id_2 = _make_issued_invoice("second")

        session.commit()

        return {
            "currency_id": str(currency.id),
            "customer_id": str(customer.id),
            "invoice_id_1": invoice_id_1,
            "invoice_id_2": invoice_id_2,
        }
    finally:
        session.close()


# ----------------------------------------------------------------- Tests


@requires_database
def test_full_payment_to_single_invoice(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    payment_fixtures: dict,
) -> None:
    """Pay an entire invoice (500.0000) in one payment -> invoice becomes PAID."""
    resp = client.post(
        "/api/v1/payments",
        json={
            "customer_id": payment_fixtures["customer_id"],
            "currency_id": payment_fixtures["currency_id"],
            "amount": "500.0000",
            "method": "BANK_TRANSFER",
            "allocations": [
                {
                    "invoice_id": payment_fixtures["invoice_id_1"],
                    "allocated_amount": "500.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount"] == "500.0000"
    assert body["unallocated_amount"] == "0.0000"
    assert len(body["allocations"]) == 1
    assert body["allocations"][0]["allocated_amount"] == "500.0000"

    # Verify invoice is PAID.
    inv_resp = client.get(
        f"/api/v1/invoices/{payment_fixtures['invoice_id_1']}",
        headers=manage_auth_headers,
    )
    assert inv_resp.status_code == 200, inv_resp.text
    assert inv_resp.json()["state"] == "PAID"
    assert inv_resp.json()["amount_paid"] == "500.0000"
    assert inv_resp.json()["balance_due"] == "0.0000"


@requires_database
def test_split_payment_across_two_invoices(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    payment_fixtures: dict,
) -> None:
    """Pay 300 total, split across two invoices (200 + 100)."""
    resp = client.post(
        "/api/v1/payments",
        json={
            "customer_id": payment_fixtures["customer_id"],
            "currency_id": payment_fixtures["currency_id"],
            "amount": "300.0000",
            "method": "CASH",
            "allocations": [
                {
                    "invoice_id": payment_fixtures["invoice_id_1"],
                    "allocated_amount": "200.0000",
                },
                {
                    "invoice_id": payment_fixtures["invoice_id_2"],
                    "allocated_amount": "100.0000",
                },
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["unallocated_amount"] == "0.0000"
    assert len(body["allocations"]) == 2

    # Both invoices should be PARTIALLY_PAID.
    for inv_id in [payment_fixtures["invoice_id_1"], payment_fixtures["invoice_id_2"]]:
        inv_resp = client.get(f"/api/v1/invoices/{inv_id}", headers=manage_auth_headers)
        assert inv_resp.status_code == 200, inv_resp.text
        assert inv_resp.json()["state"] == "PARTIALLY_PAID"


@requires_database
def test_allocation_exceeds_payment_amount_rejected(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    payment_fixtures: dict,
) -> None:
    """Trying to allocate more than the payment amount must be rejected (422)."""
    resp = client.post(
        "/api/v1/payments",
        json={
            "customer_id": payment_fixtures["customer_id"],
            "currency_id": payment_fixtures["currency_id"],
            "amount": "100.0000",
            "method": "CARD",
            "allocations": [
                {
                    "invoice_id": payment_fixtures["invoice_id_1"],
                    "allocated_amount": "200.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 422, resp.text


@requires_database
def test_allocation_exceeds_invoice_balance_rejected(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    payment_fixtures: dict,
) -> None:
    """Trying to allocate more than invoice.balance_due must be rejected (422)."""
    resp = client.post(
        "/api/v1/payments",
        json={
            "customer_id": payment_fixtures["customer_id"],
            "currency_id": payment_fixtures["currency_id"],
            "amount": "1000.0000",
            "method": "BANK_TRANSFER",
            "allocations": [
                {
                    "invoice_id": payment_fixtures["invoice_id_1"],
                    "allocated_amount": "600.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 422, resp.text
