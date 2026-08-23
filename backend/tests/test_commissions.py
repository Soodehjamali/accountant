"""Tests for the commission endpoints and service.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_invoices.py``).  Builds its
own supporting rows directly via the ORM/service layer.

Test matrix:
* Create a config and resolve it for a matching order
* Fallback to broader (global) config when no specific match
* Commission transaction amount calculation (rate * grand_total)
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
    reason="DATABASE_URL is not set; skipping live DB commission tests",
)

COMMISSION_MANAGE = "COMMISSION_MANAGE"


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
        username = f"test_comm_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"COMMISSION_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Commission Tester (test)", created_by=system_user.id
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session,
                    code=code,
                    name=code,
                    resource="commission",
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
    return _user_with_permissions(COMMISSION_MANAGE)


@pytest.fixture()
def commission_fixtures() -> dict:
    """All supporting rows plus a completed order (grand_total=500)."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]
        product = Product(
            sku=f"SKU-COMM-{suffix}",
            name="Commission Test Product",
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
            code=f"REP-COMM-{suffix}",
            person_name="Commission Test Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-COMM-{suffix}",
            name="Commission Test Customer",
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

        # Create and complete an order through the full lifecycle.
        from services import invoice_service, order_service

        order = order_service.create_order(
            session,
            customer_id=customer.id,
            representative_id=representative.id,
            currency_id=currency.id,
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

        # Pay the invoice fully so we can mark completed.
        invoice_service.record_payment(
            session, invoice.id, amount=decimal.Decimal("500.0000"), actor_user_id=system_user.id
        )
        session.refresh(invoice)
        assert invoice.state == "PAID", f"Invoice state is {invoice.state}"

        order_service.mark_completed(session, order.id, actor_user_id=system_user.id)
        session.refresh(order)
        assert order.state == "COMPLETED", f"Order state is {order.state}"

        session.commit()

        return {
            "currency_id": str(currency.id),
            "customer_id": str(customer.id),
            "representative_id": str(representative.id),
            "order_id": str(order.id),
        }
    finally:
        session.close()


# ----------------------------------------------------------------- Tests


@requires_database
def test_create_config_and_resolve_for_matching_order(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    commission_fixtures: dict,
) -> None:
    """Create a specific commission config and verify it resolves correctly."""
    # Create config for this representative, LOCAL orders, 10% rate.
    resp = client.post(
        "/api/v1/commission-configs",
        json={
            "rate": "10.0000",
            "effective_from": "2024-01-01T00:00:00Z",
            "representative_id": commission_fixtures["representative_id"],
            "order_type": "LOCAL",
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    config = resp.json()
    assert config["rate"] == "10.0000"
    assert config["order_type"] == "LOCAL"

    # Calculate commission for the completed order (grand_total=500, rate=10%).
    resp = client.post(
        f"/api/v1/orders/{commission_fixtures['order_id']}/commission",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    txn = resp.json()
    assert txn["signed_amount"] == "50.0000"  # 10% of 500
    assert txn["rate_applied"] == "10.0000"
    assert txn["state_event"] == "ACCRUED"


@requires_database
def test_fallback_to_global_config(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    commission_fixtures: dict,
) -> None:
    """When no specific config exists, fall back to global (NULL representative) config."""
    # Create a global config (representative_id=NULL) for LOCAL orders, 5% rate.
    resp = client.post(
        "/api/v1/commission-configs",
        json={
            "rate": "5.0000",
            "effective_from": "2024-01-01T00:00:00Z",
            "order_type": "LOCAL",
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text

    # Calculate commission -- should fall back to the global config.
    resp = client.post(
        f"/api/v1/orders/{commission_fixtures['order_id']}/commission",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    txn = resp.json()
    assert txn["signed_amount"] == "25.0000"  # 5% of 500
    assert txn["rate_applied"] == "5.0000"
    assert txn["state_event"] == "ACCRUED"


@requires_database
def test_commission_amount_calculation(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    commission_fixtures: dict,
) -> None:
    """Verify commission amount = rate * grand_total for a non-trivial rate."""
    # Create config with 7.5% rate.
    resp = client.post(
        "/api/v1/commission-configs",
        json={
            "rate": "7.5000",
            "effective_from": "2024-01-01T00:00:00Z",
            "order_type": "LOCAL",
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text

    # Calculate commission.
    resp = client.post(
        f"/api/v1/orders/{commission_fixtures['order_id']}/commission",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    txn = resp.json()
    # 7.5% of 500 = 37.5
    assert txn["signed_amount"] == "37.5000"
    assert txn["rate_applied"] == "7.5000"
    assert txn["representative_id"] == commission_fixtures["representative_id"]
    assert txn["order_id"] == commission_fixtures["order_id"]
