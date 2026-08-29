"""Focused tests for Order lifecycle completion: credit limit enforcement
and commission auto-calculation.

Covers:
1. Credit limit enforcement: order creation blocked when outstanding
   balance + new order exceeds customer's credit_limit_amount.
2. Credit limit enforcement: order allowed when within limit.
3. Credit limit enforcement: credit_limit_amount == 0 skips the check.
4. Commission auto-calculation: commission transaction created on
   order COMPLETED.
5. Commission auto-calculation: no matching config doesn't fail the
   order completion.

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

from database.models.commission_config import CommissionConfig
from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import (
    auth_service,
    bootstrap_service,
    commission_service,
    customer_ledger_service,
    inventory_service,
    order_service,
    rbac_service,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping order lifecycle tests",
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


def _make_admin_user(suffix: str) -> dict[str, str]:
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        username = f"olc_{suffix}"
        password = "correct-horse-battery-staple"
        user = auth_service.create_user(
            session, username=username, email=f"{username}@example.invalid",
            password=password, created_by=system_user.id,
        )
        role_code = f"ROLE_OLC_{suffix}"
        rbac_service.create_role(session, code=role_code, name=f"OLC {suffix}",
                                 created_by=system_user.id)
        for code in (ORDER_MANAGE, "ORDER_APPROVE"):
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


def _create_base_fixtures(session, suffix: str) -> dict:
    """Create all FK targets (currency, warehouse, uom, product, price_list,
    price_history, representative, customer) and return them."""
    system_user = bootstrap_service.ensure_system_user(session)
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    rep = Representative(
        code=f"REP-OLC-{suffix}", person_name="OLC Rep", status="ACTIVE",
        created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(rep)

    product = Product(
        sku=f"SKU-OLC-{suffix}", name="OLC Product", base_uom_id=uom.id,
        status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(product)

    price_list = PriceList(
        name=f"PL-OLC-{suffix}", price_type="RETAIL", currency_id=currency.id,
        owner_scope="GLOBAL", is_active=True,
        created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(price_list)

    session.flush()  # Flush Product + PriceList so their IDs are available.

    price_history = PriceHistory(
        product_id=product.id, price_list_id=price_list.id,
        currency_id=currency.id, price_type="RETAIL",
        unit_price=decimal.Decimal("100.0000"),
        effective_from=datetime.datetime.now(datetime.timezone.utc),
        created_by=system_user.id,
    )
    session.add(price_history)

    # Stock for reservation
    inventory_service.post_transaction(
        session, product_id=product.id, warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal("1000"),
        unit_cost=decimal.Decimal("50.0000"),
        currency_id=currency.id, actor_user_id=system_user.id,
    )

    session.flush()
    return {
        "system_user": system_user,
        "currency": currency,
        "warehouse": warehouse,
        "rep": rep,
        "product": product,
        "price_list": price_list,
        "price_history": price_history,
    }


def _create_customer(session, fx: dict, suffix: str, credit_limit: str = "0") -> Customer:
    customer = Customer(
        code=f"CUST-OLC-{suffix}", name="OLC Customer", type="CORPORATE",
        currency_id=fx["currency"].id, status="ACTIVE",
        credit_limit_amount=decimal.Decimal(credit_limit),
        created_by=fx["system_user"].id, updated_by=fx["system_user"].id,
    )
    session.add(customer)
    session.flush()
    return customer


def _create_order(session, fx: dict, customer: Customer) -> order_service.Order:
    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=fx["rep"].id,
        currency_id=fx["currency"].id,
        price_list_id=fx["price_list"].id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            order_service.OrderLineInput(
                product_id=fx["product"].id,
                fulfillment_warehouse_id=fx["warehouse"].id,
                price_history_id=fx["price_history"].id,
                qty_ordered=decimal.Decimal("5"),
                fulfillment_mode="REP_LOCAL",
            )
        ],
        created_by=fx["system_user"].id,
    )
    return order


def _advance_to_completed(session, order: order_service.Order, fx: dict) -> order_service.Order:
    """Advance an order through the full lifecycle to COMPLETED."""
    order_service.submit_order(session, order.id, actor_user_id=fx["system_user"].id)
    order_service.approve_order(session, order.id, actor_user_id=fx["system_user"].id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=fx["system_user"].id)
    order_service.start_fulfillment(session, order.id, actor_user_id=fx["system_user"].id)
    order_line = list(order_service.list_order_lines(session, order.id))[0]
    order_service.ship_order(
        session, order.id,
        shipments=[order_service.ShipmentInput(
            order_line_id=order_line.id, quantity=decimal.Decimal("5")
        )],
        actor_user_id=fx["system_user"].id,
    )
    session.flush()
    session.refresh(order)
    assert order.state == "SHIPPED"
    return order


# ---------------------------------------------------------------------------
# Credit limit tests
# ---------------------------------------------------------------------------

@requires_database
class TestCreditLimitEnforcement:
    """Credit limit is checked at order creation time."""

    def test_order_blocked_when_exceeding_credit_limit(self):
        """Order creation fails when outstanding + new order > credit_limit."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            suffix = uuid.uuid4().hex[:8]
            fx = _create_base_fixtures(session, suffix)

            # Customer with credit limit of 300
            customer = _create_customer(session, fx, suffix, credit_limit="300")
            session.flush()  # flush customer so ensure_customer_ledger can find it

            # Post an invoice entry (debit = 250) to create outstanding balance
            customer_ledger_service.ensure_customer_ledger(session, customer_id=customer.id,
                                                           currency_id=fx["currency"].id)
            customer_ledger_service.record_entry(
                session,
                customer_id=customer.id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal("250"),
                currency_id=fx["currency"].id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=fx["system_user"].id,
            )
            session.flush()

            # New order for 100 (5 units @ 100) would make total 350 > 300
            with pytest.raises(order_service.CustomerCreditLimitExceededError):
                _create_order(session, fx, customer)
        finally:
            session.close()

    def test_order_allowed_when_within_credit_limit(self):
        """Order creation succeeds when outstanding + new order <= credit_limit."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            suffix = uuid.uuid4().hex[:8]
            fx = _create_base_fixtures(session, suffix)

            # Customer with credit limit of 1000
            customer = _create_customer(session, fx, suffix, credit_limit="1000")
            session.flush()  # flush customer

            # Post an invoice entry (debit = 200)
            customer_ledger_service.ensure_customer_ledger(session, customer_id=customer.id,
                                                           currency_id=fx["currency"].id)
            customer_ledger_service.record_entry(
                session,
                customer_id=customer.id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal("200"),
                currency_id=fx["currency"].id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=fx["system_user"].id,
            )
            session.flush()

            # New order for 100 (5 units @ 100) makes total 300 <= 1000
            order = _create_order(session, fx, customer)
            session.commit()
            assert order.state == "DRAFT"
        finally:
            session.close()

    def test_zero_credit_limit_skips_check(self):
        """credit_limit_amount == 0 means no limit configured; check is skipped."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            suffix = uuid.uuid4().hex[:8]
            fx = _create_base_fixtures(session, suffix)

            # Customer with default credit limit (0)
            customer = _create_customer(session, fx, suffix, credit_limit="0")

            # Even with outstanding balance, order is allowed
            order = _create_order(session, fx, customer)
            session.commit()
            assert order.state == "DRAFT"
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Commission auto-calculation tests
# ---------------------------------------------------------------------------

@requires_database
class TestCommissionAutoCalculation:
    """Commission is automatically calculated when order reaches COMPLETED."""

    def test_commission_created_on_order_completed(self):
        """A commission transaction is created when order reaches COMPLETED."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            suffix = uuid.uuid4().hex[:8]
            fx = _create_base_fixtures(session, suffix)
            customer = _create_customer(session, fx, suffix, credit_limit="0")

            # Create a commission config (5% for LOCAL orders)
            config = CommissionConfig(
                order_type="LOCAL",
                rate=decimal.Decimal("5.00"),
                effective_from=datetime.datetime.now(datetime.timezone.utc),
                created_by=fx["system_user"].id,
                updated_by=fx["system_user"].id,
            )
            session.add(config)
            session.flush()

            # Create and advance order to SHIPPED
            order = _create_order(session, fx, customer)
            order = _advance_to_completed(session, order, fx)

            # Mark as invoiced and paid (to reach COMPLETED)
            order_service.mark_invoiced(session, order.id, actor_user_id=fx["system_user"].id)
            order_service.mark_paid(session, order.id, actor_user_id=fx["system_user"].id)
            order_service.mark_completed(session, order.id, actor_user_id=fx["system_user"].id)
            session.flush()
            session.refresh(order)
            assert order.state == "COMPLETED"

            # Verify commission transaction was created
            commission = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order.id
                )
            ).scalar_one_or_none()
            assert commission is not None
            assert commission.state_event == "ACCRUED"
            # 5% of 500 (5 units @ 100) = 25
            assert commission.signed_amount == decimal.Decimal("25.00")
        finally:
            session.close()

    def test_no_commission_config_doesnt_fail_order(self):
        """Order completion succeeds even when no matching commission config exists."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            suffix = uuid.uuid4().hex[:8]
            fx = _create_base_fixtures(session, suffix)
            customer = _create_customer(session, fx, suffix, credit_limit="0")

            # Create and advance order to SHIPPED
            order = _create_order(session, fx, customer)
            order = _advance_to_completed(session, order, fx)

            # Mark as invoiced and paid (to reach COMPLETED)
            order_service.mark_invoiced(session, order.id, actor_user_id=fx["system_user"].id)
            order_service.mark_paid(session, order.id, actor_user_id=fx["system_user"].id)
            order_service.mark_completed(session, order.id, actor_user_id=fx["system_user"].id)
            session.flush()
            session.refresh(order)

            # The key assertion: order reaches COMPLETED even if commission
            # config doesn't match or calculation fails (best-effort).
            assert order.state == "COMPLETED"
        finally:
            session.close()
