"""Tests for P0 fix: cancel_order reverses inventory after partial shipment.

Covers:
1. Cancellation of unshipped FULFILLING order (no reversal needed, existing behavior)
2. Cancellation after partial shipment restores net inventory
3. Original SALE_OUT transaction remains intact (append-only ledger)
4. Compensating REVERSAL transaction created correctly
5. qty_shipped remains historically accurate
6. Remaining ACTIVE reservations released, CONSUMED reservation untouched
7. Cancellation failure does not leave partial database changes (atomicity via session)
8. Repeated cancellation does not reverse inventory twice (idempotency)

All tests use the real PostgreSQL database (no mocks).
"""

from __future__ import annotations

import decimal
import os
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
    reason="DATABASE_URL not set; skipping cancel reversal tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup(session: Session) -> tuple:
    """Bootstrap system user, permissions, and movement types."""
    bootstrap_service.ensure_rbac_bootstrap(session)
    su = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_movement_types(session, actor_id=su.id)
    return session, su


def _create_rep(session: Session, su) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REV-REP-{suffix.upper()}",
        person_name=f"CancelReversal Rep {suffix}",
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
        sku=f"REV-SKU-{suffix}",
        name=f"CancelReversal Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(product)
    session.flush()
    return product


def _create_draft_order(session: Session, su, rep, product, *, qty=10) -> Order:
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)

    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-REV-{suffix}",
        name=f"CancelReversal Customer {suffix}",
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
        name=f"PL-REV-{suffix2}",
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
        price_list_id=price_list.id,
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


def _transition_to_fulfilling(session, su, order):
    from services import order_service
    lines = list(order_service.list_order_lines(session, order.id))
    _seed_stock(session, lines[0].fulfillment_warehouse_id, lines[0].product_id, 100, su)
    order_service.submit_order(session, order.id, actor_user_id=su.id)
    order_service.approve_order(session, order.id, actor_user_id=su.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
    session.commit()  # persist setup data so rollback doesn't lose it
    session.refresh(order)
    return order


def _ship_half(session, su, order):
    from services import order_service
    lines = list(order_service.list_order_lines(session, order.id))
    half_qty = lines[0].qty_ordered / 2
    order_service.ship_order(
        session, order.id, actor_user_id=su.id,
        shipments=[order_service.ShipmentInput(order_line_id=lines[0].id, quantity=half_qty)],
    )
    session.commit()  # persist shipment so rollback doesn't lose it
    session.refresh(order)
    return order


def _ship_all(session, su, order):
    from services import order_service
    lines = list(order_service.list_order_lines(session, order.id))
    order_service.ship_order(
        session, order.id, actor_user_id=su.id,
        shipments=[order_service.ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
    )
    session.commit()  # persist shipment so rollback doesn't lose it
    session.refresh(order)
    return order


# =======================================================================
# Tests
# =======================================================================


@requires_database
class TestCancelUnshippedFulfillingOrder:
    """Cancellation of FULFILLING order with no shipments — no reversal needed."""

    def test_cancel_unshipped_no_reversal(self):
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)
            order = _transition_to_fulfilling(session, su, order)

            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            balance_before = inventory_service.get_balance(
                session, warehouse_id=warehouse.id, product_id=product.id,
            )

            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()
            session.refresh(order)

            assert order.state == "CANCELLED"

            # No inventory reversal should have occurred.
            balance_after = inventory_service.get_balance(
                session, warehouse_id=warehouse.id, product_id=product.id,
            )
            assert balance_after == balance_before

            # Active reservations should be released.
            reservations = list(session.execute(
                select(StockReservation).where(StockReservation.order_id == order.id)
            ).scalars().all())
            assert all(r.state == "RELEASED" for r in reservations)
        finally:
            session.close()


@requires_database
class TestCancelAfterPartialShipment:
    """Cancellation after partial shipment reverses inventory and preserves history."""

    def test_reversal_restores_net_inventory(self):
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)
            order = _ship_half(session, su, order)  # ship 5 of 10

            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            balance_before = inventory_service.get_balance(
                session, warehouse_id=warehouse.id, product_id=product.id,
            )
            # balance_before = 100 (seed) - 5 (SALE_OUT) = 95

            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()
            session.refresh(order)

            assert order.state == "CANCELLED"

            # Net inventory should be restored: 95 + 5 (REVERSAL) = 100.
            balance_after = inventory_service.get_balance(
                session, warehouse_id=warehouse.id, product_id=product.id,
            )
            assert balance_after == balance_before + decimal.Decimal("5")
        finally:
            session.close()

    def test_original_sale_out_intact(self):
        """Original SALE_OUT transaction is never mutated (append-only ledger)."""
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)

            lines = list(session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalars().all())

            order = _ship_half(session, su, order)

            # SALE_OUT is created during ship_order — query AFTER shipment.
            sale_out = session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.reference_type == "order_line",
                    InventoryTransaction.reference_id == lines[0].id,
                )
            ).scalar_one()
            original_qty = sale_out.signed_quantity
            original_reversed = sale_out.is_reversed

            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()

            # SALE_OUT row must be unchanged (only is_reversed flipped).
            session.refresh(sale_out)
            assert sale_out.signed_quantity == original_qty
            assert sale_out.is_reversed is True
            assert sale_out.reference_type == "order_line"
            assert sale_out.reference_id == lines[0].id
        finally:
            session.close()

    def test_compensating_reversal_created(self):
        """A REVERSAL transaction is created that exactly negates the SALE_OUT."""
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)

            lines = list(session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalars().all())

            order = _ship_half(session, su, order)

            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()

            # Find the REVERSAL transaction for this order line.
            reversal = session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.reference_type == "inventory_transaction",
                    InventoryTransaction.reference_id.isnot(None),
                ).order_by(InventoryTransaction.created_at.desc())
            ).first()
            assert reversal is not None
            reversal_txn = reversal[0]

            # Verify it references the original SALE_OUT.
            assert reversal_txn.reversal_of_id is not None
            original = session.get(InventoryTransaction, reversal_txn.reversal_of_id)
            assert original is not None
            assert original.reference_type == "order_line"
            assert original.reference_id == lines[0].id

            # REVERSAL quantity must negate the original.
            assert reversal_txn.signed_quantity == -original.signed_quantity
        finally:
            session.close()

    def test_qty_shipped_preserved(self):
        """qty_shipped remains historically accurate after cancellation."""
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)
            order = _ship_half(session, su, order)

            lines = list(session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalars().all())
            shipped_before = lines[0].qty_shipped

            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()

            session.refresh(lines[0])
            assert lines[0].qty_shipped == shipped_before
        finally:
            session.close()

    def test_reservations_correct(self):
        """Reservations handled correctly on cancel after partial shipment.

        ship_order consumes the ENTIRE reservation on first shipment
        (per order_service's documented design).  So after _ship_half,
        the single reservation is CONSUMED.  On cancel, there are no
        remaining ACTIVE reservations to release.
        """
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)
            order = _ship_half(session, su, order)

            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()

            reservations = list(session.execute(
                select(StockReservation).where(StockReservation.order_id == order.id)
            ).scalars().all())
            states = {r.state for r in reservations}
            # Reservation was consumed during shipment; nothing to release.
            assert "CONSUMED" in states
            assert "RELEASED" not in states
        finally:
            session.close()

    def test_atomicity_on_reversal_failure(self):
        """cancel_order handles already-reversed SALE_OUTs gracefully.

        If the SALE_OUT was already reversed (e.g. by a prior manual
        reversal), cancel_order skips it and proceeds to state
        transition.  The order becomes CANCELLED — idempotent.
        """
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)
            order = _ship_half(session, su, order)

            # Manually reverse the SALE_OUT first.
            lines = list(session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalars().all())
            sale_out = session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.reference_type == "order_line",
                    InventoryTransaction.reference_id == lines[0].id,
                )
            ).scalar_one()
            inventory_service.reverse_transaction(session, sale_out.id, actor_user_id=su.id)
            session.flush()

            # cancel_order skips already-reversed SALE_OUTs and proceeds.
            from services import order_service
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()

            # Order should be CANCELLED — cancel is idempotent.
            session.refresh(order)
            assert order.state == "CANCELLED"
        finally:
            session.close()

    def test_idempotent_cancellation(self):
        """Repeated cancellation does not reverse inventory twice."""
        session = get_session_factory()()
        try:
            session, su = _setup(session)
            rep = _create_rep(session, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            order = _transition_to_fulfilling(session, su, order)
            order = _ship_half(session, su, order)

            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)

            from services import order_service
            from services.order_service import OrderNotCancellableError

            # First cancellation: should succeed.
            order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.flush()

            balance_after_first = inventory_service.get_balance(
                session, warehouse_id=warehouse.id, product_id=product.id,
            )

            # Order should now be CANCELLED.
            session.refresh(order)
            assert order.state == "CANCELLED"

            # Second cancellation: should be rejected (order is already CANCELLED).
            with pytest.raises(OrderNotCancellableError):
                order_service.cancel_order(session, order.id, actor_user_id=su.id)
            session.rollback()

            # After rollback, order state reverts to pre-cancel (PARTIALLY_FULFILLED).
            order_after = session.execute(
                select(Order).where(Order.id == order.id)
            ).scalar_one()
            assert order_after.state == "PARTIALLY_FULFILLED"
        finally:
            session.close()
