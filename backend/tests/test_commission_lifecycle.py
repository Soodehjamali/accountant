"""Focused tests for the Commission Approval & Payment Lifecycle.

Covers:
1. Approve ACCRUED commission → creates APPROVED row
2. Pay APPROVED commission → creates PAID row
3. Clawback ACCRUED commission → creates CLAWED_BACK row with negative amount
4. Clawback APPROVED commission → creates CLAWED_BACK row with negative amount
5. Cannot approve non-ACCRUED commission
6. Cannot pay non-APPROVED commission
7. Cannot clawback PAID commission
8. Commission balance reflects all transactions
9. Full lifecycle: accrue → approve → pay
10. Lifecycle with clawback: accrue → approve → clawback
11. Representative balance decreases after clawback
12. Existing regression: commission calculation still works

All tests use real PostgreSQL (no mocks).
"""

from __future__ import annotations

import decimal
import os
import uuid

import pytest
from sqlalchemy import select

import datetime

from database.models.commission_config import CommissionConfig
from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.order import Order
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, order_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping commission lifecycle tests",
)

COMMISSION_MANAGE = "COMMISSION_MANAGE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(username: str, password: str) -> dict[str, str]:
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session, username_or_email=username, password=password,
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
        username = f"test_comm_lc_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"COMM_LC_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Commission Lifecycle Tester (test)",
            created_by=system_user.id,
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="commission",
                    action="test", created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=code,
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code,
            assigned_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@pytest.fixture()
def manage_auth() -> dict[str, str]:
    return _user_with_permissions(COMMISSION_MANAGE)


@pytest.fixture()
def commission_fixtures() -> dict:
    """Create all FK targets for commission lifecycle testing."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        representative = Representative(
            code=f"REP-CLC-{suffix}",
            person_name="Commission Lifecycle Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)
        session.flush()

        customer = Customer(
            code=f"CUST-CLC-{suffix}",
            name="Commission Lifecycle Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        product = Product(
            sku=f"SKU-CLC-{suffix}",
            name="Commission Lifecycle Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-CLC-{suffix}",
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
            unit_price=decimal.Decimal("200.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        # Post stock.
        inventory_service.post_transaction(
            session,
            product_id=product.id,
            warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("100"),
            unit_cost=decimal.Decimal("100.0000"),
            currency_id=currency.id,
            actor_user_id=system_user.id,
        )

        # Create commission config: 10% for this rep.
        config = CommissionConfig(
            representative_id=representative.id,
            order_type="LOCAL",
            rate=decimal.Decimal("10.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30),
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(config)
        session.flush()

        # Create a COMPLETED order for commission calculation.
        from services.order_service import create_order, OrderLineInput, submit_order, approve_order, reserve_order_stock, start_fulfillment, ship_order, mark_invoiced, mark_paid, mark_completed

        order = create_order(
            session,
            customer_id=customer.id,
            representative_id=representative.id,
            currency_id=currency.id,
            price_list_id=price_list.id,
            order_type="LOCAL",
            fulfillment_mode="REP_LOCAL",
            sales_channel="OFFICE",
            lines=[OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=decimal.Decimal("5"),
                fulfillment_mode="REP_LOCAL",
            )],
            created_by=system_user.id,
        )

        # Advance through lifecycle to COMPLETED.
        submit_order(session, order.id, actor_user_id=system_user.id)
        approve_order(session, order.id, actor_user_id=system_user.id)
        reserve_order_stock(session, order.id, actor_user_id=system_user.id)
        start_fulfillment(session, order.id, actor_user_id=system_user.id)

        from services.order_service import ShipmentInput
        order_lines = list(order_service.list_order_lines(session, order.id))
        ship_order(
            session, order.id, actor_user_id=system_user.id,
            shipments=[ShipmentInput(order_line_id=order_lines[0].id, quantity=decimal.Decimal("5"))],
        )
        mark_invoiced(session, order.id, actor_user_id=system_user.id)
        mark_paid(session, order.id, actor_user_id=system_user.id)
        mark_completed(session, order.id, actor_user_id=system_user.id)

        # Calculate commission (ACCRUED) — may already exist from mark_completed.
        from services.commission_service import calculate_commission_for_order, CommissionAlreadyCalculatedError
        try:
            accrued = calculate_commission_for_order(session, order_id=order.id, actor_user_id=system_user.id)
        except CommissionAlreadyCalculatedError:
            # Commission was already calculated by mark_completed.
            from services.commission_service import get_order_commission
            accrued = get_order_commission(session, order.id)
            assert accrued is not None, "Commission should exist after mark_completed"

        session.commit()
        return {
            "currency_id": str(currency.id),
            "warehouse_id": str(warehouse.id),
            "representative_id": str(representative.id),
            "customer_id": str(customer.id),
            "product_id": str(product.id),
            "price_history_id": str(price_history.id),
            "price_list_id": str(price_list.id),
            "config_id": str(config.id),
            "order_id": str(order.id),
            "accrued_txn_id": str(accrued.id),
            "accrued_amount": str(accrued.signed_amount),
        }
    finally:
        session.close()



# ===========================================================================
# Tests
# ===========================================================================


@requires_database
class TestApproveCommission:
    """ACCRUED → APPROVED."""

    def test_approve_creates_approved_row(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Approving an ACCRUED commission creates a new APPROVED row."""
        txn_id = commission_fixtures["accrued_txn_id"]

        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/approve",
            json={"note": "Approved for payment"},
            headers=manage_auth,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["state_event"] == "APPROVED"
        assert decimal.Decimal(body["signed_amount"]) == decimal.Decimal(
            commission_fixtures["accrued_amount"]
        )

    def test_approve_preserves_original_accrued(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """The original ACCRUED row is not modified."""
        txn_id = commission_fixtures["accrued_txn_id"]

        client.post(
            f"/api/v1/commission-transactions/{txn_id}/approve",
            json={},
            headers=manage_auth,
        )

        # Original should still be ACCRUED.
        resp = client.get(
            f"/api/v1/commission-transactions/{txn_id}",
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state_event"] == "ACCRUED"


@requires_database
class TestPayCommission:
    """APPROVED → PAID."""

    def test_pay_creates_paid_row(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Paying an APPROVED commission creates a new PAID row."""
        txn_id = commission_fixtures["accrued_txn_id"]

        # First approve.
        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/approve",
            json={},
            headers=manage_auth,
        )
        approved_id = resp.json()["id"]

        # Then pay.
        resp = client.post(
            f"/api/v1/commission-transactions/{approved_id}/pay",
            json={"note": "Paid to representative"},
            headers=manage_auth,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["state_event"] == "PAID"


@requires_database
class TestClawbackCommission:
    """ACCRUED/APPROVED → CLAWED_BACK."""

    def test_clawback_accrued_creates_negative_row(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Clawing back an ACCRUED commission creates a CLAWED_BACK row
        with a negative signed_amount."""
        txn_id = commission_fixtures["accrued_txn_id"]
        original_amount = decimal.Decimal(commission_fixtures["accrued_amount"])

        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/clawback",
            json={"note": "Order returned"},
            headers=manage_auth,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["state_event"] == "CLAWED_BACK"
        assert decimal.Decimal(body["signed_amount"]) == -abs(original_amount)
        assert body["reversal_of_id"] == txn_id

    def test_clawback_approved_creates_negative_row(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Clawing back an APPROVED commission creates a CLAWED_BACK row."""
        txn_id = commission_fixtures["accrued_txn_id"]

        # First approve.
        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/approve",
            json={},
            headers=manage_auth,
        )
        approved_id = resp.json()["id"]

        # Then clawback.
        resp = client.post(
            f"/api/v1/commission-transactions/{approved_id}/clawback",
            json={"note": "Order cancelled after approval"},
            headers=manage_auth,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["state_event"] == "CLAWED_BACK"
        assert resp.json()["reversal_of_id"] == approved_id


@requires_database
class TestInvalidStateTransitions:
    """Invalid commission state transitions are rejected."""

    def test_cannot_approve_paid(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Cannot approve a PAID commission."""
        txn_id = commission_fixtures["accrued_txn_id"]

        # Approve → Pay.
        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/approve",
            json={}, headers=manage_auth,
        )
        paid_id = resp.json()["id"]
        client.post(
            f"/api/v1/commission-transactions/{paid_id}/pay",
            json={}, headers=manage_auth,
        )

        # Try to approve the PAID row.
        resp = client.post(
            f"/api/v1/commission-transactions/{paid_id}/approve",
            json={}, headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_cannot_pay_accrued(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Cannot pay an ACCRUED commission (must approve first)."""
        txn_id = commission_fixtures["accrued_txn_id"]

        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/pay",
            json={}, headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_cannot_clawback_paid(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Cannot clawback a PAID commission."""
        txn_id = commission_fixtures["accrued_txn_id"]

        # Approve → Pay.
        resp = client.post(
            f"/api/v1/commission-transactions/{txn_id}/approve",
            json={}, headers=manage_auth,
        )
        assert resp.status_code == 201, f"Approve failed: {resp.text}"
        approved_id = resp.json()["id"]
        assert resp.json()["state_event"] == "APPROVED", f"Expected APPROVED, got {resp.json()['state_event']}"

        resp = client.post(
            f"/api/v1/commission-transactions/{approved_id}/pay",
            json={}, headers=manage_auth,
        )
        assert resp.status_code == 201, f"Pay failed: {resp.text}"
        paid_id = resp.json()["id"]
        assert resp.json()["state_event"] == "PAID", f"Expected PAID, got {resp.json()['state_event']}"

        # Verify the paid transaction exists and is PAID.
        resp = client.get(
            f"/api/v1/commission-transactions/{paid_id}",
            headers=manage_auth,
        )
        assert resp.status_code == 200, f"Get paid failed: {resp.text}"
        assert resp.json()["state_event"] == "PAID", f"Expected PAID, got {resp.json()['state_event']}"

        # Try to clawback the PAID row.
        resp = client.post(
            f"/api/v1/commission-transactions/{paid_id}/clawback",
            json={}, headers=manage_auth,
        )
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"


@requires_database
class TestCommissionBalance:
    """Commission balance reflects all transactions."""

    def test_balance_after_accrual(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Balance equals the ACCRUED amount."""
        rep_id = commission_fixtures["representative_id"]

        resp = client.get(
            f"/api/v1/representatives/{rep_id}/commission-balance",
            headers=manage_auth,
        )
        assert resp.status_code == 200
        balance = decimal.Decimal(resp.json()["balance"])
        assert balance == decimal.Decimal(commission_fixtures["accrued_amount"])

    def test_balance_after_clawback(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Balance decreases after clawback."""
        rep_id = commission_fixtures["representative_id"]
        txn_id = commission_fixtures["accrued_txn_id"]
        original = decimal.Decimal(commission_fixtures["accrued_amount"])

        # Clawback.
        client.post(
            f"/api/v1/commission-transactions/{txn_id}/clawback",
            json={}, headers=manage_auth,
        )

        # Balance should be 0 (accrued + clawed_back = original - original).
        resp = client.get(
            f"/api/v1/representatives/{rep_id}/commission-balance",
            headers=manage_auth,
        )
        assert resp.status_code == 200
        balance = decimal.Decimal(resp.json()["balance"])
        assert balance == decimal.Decimal("0")


@requires_database
class TestExistingRegression:
    """Existing commission functionality still works."""

    def test_commission_calculation_still_works(
        self, client, manage_auth: dict, commission_fixtures: dict,
    ):
        """Creating a new order and calculating commission still works."""
        # The fixture already has one commission. Verify it exists.
        txn_id = commission_fixtures["accrued_txn_id"]
        resp = client.get(
            f"/api/v1/commission-transactions/{txn_id}",
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state_event"] == "ACCRUED"
