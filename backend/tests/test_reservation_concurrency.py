"""Concurrency tests for reserve_order_stock() TOCTOU race fix.

Tests whether two concurrent reservations competing for the same limited
stock can over-reserve.

Uses two separate database sessions to simulate true concurrency.
"""

from __future__ import annotations

import decimal
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.inventory_transaction import InventoryTransaction
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.stock_reservation import StockReservation
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping reservation concurrency tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup(session: Session):
    bootstrap_service.ensure_rbac_bootstrap(session)
    su = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_movement_types(session, actor_id=su.id)
    return su


def _create_rep(session: Session, su) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"RC-REP-{suffix.upper()}",
        person_name=f"ResConcurrency Rep {suffix}",
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_product(session: Session, su) -> Product:
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"RC-SKU-{suffix}",
        name=f"ResConcurrency Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(product)
    session.flush()
    return product


def _create_order(session: Session, su, rep, product, *, qty=10) -> Order:
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)

    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-RC-{suffix}",
        name=f"ResConcurrency Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(customer)
    session.flush()

    session.add(CustomerRepAssignment(
        customer_id=customer.id,
        representative_id=rep.id,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        priority=1,
        created_by=su.id,
        updated_by=su.id,
    ))
    session.flush()

    suffix2 = uuid.uuid4().hex[:8]
    price_list = PriceList(
        name=f"PL-RC-{suffix2}",
        price_type="RETAIL",
        currency_id=currency.id,
        owner_scope="GLOBAL",
        is_active=True,
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(price_list)
    session.flush()

    price = PriceHistory(
        product_id=product.id,
        price_list_id=price_list.id,
        currency_id=currency.id,
        price_type="RETAIL",
        unit_price=decimal.Decimal("50.0000"),
        effective_from=datetime.now(timezone.utc),
        created_by=su.id,
    )
    session.add(price)
    session.flush()

    from services.order_service import create_order, OrderLineInput
    order = create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price.id,
                qty_ordered=qty,
                fulfillment_mode="REP_LOCAL",
            ),
        ],
        created_by=su.id,
    )
    session.flush()
    return order


def _seed_stock(session, wh_id, product_id, qty, su):
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    inventory_service.post_transaction(
        session,
        product_id=product_id,
        warehouse_id=wh_id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty)),
        unit_cost=decimal.Decimal("10.000000"),
        currency_id=currency.id,
        actor_user_id=su.id,
    )
    session.flush()


def _transition_to_approved(session, su, order):
    from services import order_service
    order_service.submit_order(session, order.id, actor_user_id=su.id)
    order_service.approve_order(session, order.id, actor_user_id=su.id)
    session.flush()
    session.refresh(order)
    return order


# =======================================================================
# Tests
# =======================================================================


@requires_database
class TestReservationConcurrency:
    """Two concurrent reservations competing for the same limited stock."""

    def test_concurrent_reservations_do_not_over_reserve(self):
        """Two concurrent reserve_order_stock calls for different orders
        competing for the same5 units of stock must not over-reserve.

        Expected: exactly one reservation succeeds, the other gets
        BACKORDERED (insufficient stock). The total active reserved
        quantity must never exceed available stock.
        """
        factory = get_session_factory()

        # --- Setup: product, warehouse, stock=5 ---
        session_setup = factory()
        try:
            su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session_setup, actor_id=su.id,
            )
            _seed_stock(session_setup, warehouse.id, product.id, 5, su)

            # Create two orders, each requesting4 units.
            order_a = _create_order(session_setup, su, rep, product, qty=4)
            order_b = _create_order(session_setup, su, rep, product, qty=4)
            _transition_to_approved(session_setup, su, order_a)
            _transition_to_approved(session_setup, su, order_b)
            session_setup.commit()
            order_a_id = order_a.id
            order_b_id = order_b.id
        finally:
            session_setup.close()

        results = {"a": None, "b": None}

        def reserve_order(session_factory, order_id, label):
            session = session_factory()
            try:
                try:
                    from services import order_service
                    order = order_service.reserve_order_stock(
                        session, order_id, actor_user_id=su.id,
                    )
                    session.commit()
                    session.refresh(order)
                    results[label] = order.state
                except Exception:
                    session.rollback()
                    results[label] = "BACKORDERED"
            finally:
                session.close()

        t1 = threading.Thread(
            target=reserve_order, args=(factory, order_a_id, "a"),
        )
        t2 = threading.Thread(
            target=reserve_order, args=(factory, order_b_id, "b"),
        )

        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        # Exactly one must succeed (RESERVED), one must fail (BACKORDERED).
        succeeded = sum(1 for v in results.values() if v == "RESERVED")
        failed = sum(1 for v in results.values() if v == "BACKORDERED")
        assert succeeded == 1, (
            f"Expected exactly 1 RESERVED, got {succeeded}. Results: {results}"
        )
        assert failed == 1, (
            f"Expected exactly 1 BACKORDERED, got {failed}. Results: {results}"
        )

        # Verify: total active reservations must not exceed available stock (5).
        session_verify = factory()
        try:
            active_qty = session_verify.execute(
                select(
                    __import__("sqlalchemy").func.coalesce(
                        __import__("sqlalchemy").func.sum(
                            StockReservation.reserved_quantity
                        ),
                        0,
                    )
                ).where(
                    StockReservation.product_id == product.id,
                    StockReservation.warehouse_id == warehouse.id,
                    StockReservation.state == "ACTIVE",
                )
            ).scalar_one()
            assert active_qty <= decimal.Decimal("5"), (
                f"Total active reservations ({active_qty}) exceed "
                f"available stock (5). Over-reservation occurred!"
            )
        finally:
            session_verify.close()

    def test_reservation_succeeds_when_stock_available(self):
        """Single reservation with sufficient stock succeeds."""
        factory = get_session_factory()
        session = factory()
        try:
            su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _seed_stock(session, warehouse.id, product.id, 100, su)

            order = _create_order(session, su, rep, product, qty=10)
            order = _transition_to_approved(session, su, order)
            session.flush()

            from services import order_service
            order = order_service.reserve_order_stock(
                session, order.id, actor_user_id=su.id,
            )
            session.flush()
            session.refresh(order)

            assert order.state == "RESERVED"
        finally:
            session.close()

    def test_reservation_fails_when_stock_insufficient(self):
        """Reservation fails (BACKORDERED) when stock is insufficient."""
        factory = get_session_factory()
        session = factory()
        try:
            su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _seed_stock(session, warehouse.id, product.id, 3, su)

            order = _create_order(session, su, rep, product, qty=10)
            order = _transition_to_approved(session, su, order)
            session.flush()

            from services import order_service
            order = order_service.reserve_order_stock(
                session, order.id, actor_user_id=su.id,
            )
            session.flush()
            session.refresh(order)

            assert order.state == "BACKORDERED"
        finally:
            session.close()
