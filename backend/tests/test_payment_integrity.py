"""Tests for M-08 (idempotency) and M-12 (concurrency) fixes.

Verifies that:
1. Concurrent payment requests against the same invoice cannot overpay
2. Duplicate sequential payments are handled correctly
3. Payment exceeding remaining balance is rejected
4. Two payments whose combined amount exceeds balance are rejected
5. Failed payment causes no partial financial mutation
6. Transaction atomicity is preserved

All tests use real PostgreSQL (no mocks).
"""

from __future__ import annotations

import decimal
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.invoice import Invoice
from database.models.invoice_history import InvoiceHistory
from database.models.invoice_line import InvoiceLine
from database.models.invoice_order import InvoiceOrder
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.stock_reservation import StockReservation
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, order_service, invoice_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping payment integrity tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup(session: Session) -> tuple:
    bootstrap_service.ensure_rbac_bootstrap(session)
    su = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_movement_types(session, actor_id=su.id)
    return session, su


def _create_rep(session: Session, su) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"PI-REP-{suffix.upper()}",
        person_name=f"PaymentIntegrity Rep {suffix}",
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
        sku=f"PI-SKU-{suffix}",
        name=f"PaymentIntegrity Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(product)
    session.flush()
    return product


def _create_invoiced_order(session: Session, su, rep, product, *, qty=3, unit_price="100.0000") -> Order:
    """Create a fully shipped order and return it (ready for invoicing)."""
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)

    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-PI-{suffix}",
        name=f"PaymentIntegrity Customer {suffix}",
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
        name=f"PL-PI-{suffix2}",
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
        unit_price=decimal.Decimal(unit_price),
        effective_from=datetime.now(timezone.utc),
        created_by=su.id,
    )
    session.add(price)
    session.flush()

    # Seed stock
    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal("1000"),
        unit_cost=decimal.Decimal("10.000000"),
        currency_id=currency.id,
        actor_user_id=su.id,
    )
    session.flush()

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
                price_history_id=price.id,
                qty_ordered=qty,
                fulfillment_mode="REP_LOCAL",
            ),
        ],
        created_by=su.id,
    )
    order_service.submit_order(session, order.id, actor_user_id=su.id)
    order_service.approve_order(session, order.id, actor_user_id=su.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=su.id)

    lines = list(order_service.list_order_lines(session, order.id))
    order_service.ship_order(
        session, order.id, actor_user_id=su.id,
        shipments=[order_service.ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
    )
    session.flush()
    return order, customer, currency


def _create_invoice(session: Session, su, order) -> Invoice:
    """Create and issue an invoice from a shipped order."""
    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=su.id,
    )
    invoice_service.issue_invoice(session, invoice.id, actor_user_id=su.id)
    session.flush()
    session.refresh(invoice)
    assert invoice.state == "ISSUED"
    return invoice


# =======================================================================
# Tests
# =======================================================================


@requires_database
class TestPaymentConcurrency:
    """Concurrent payment requests against the same invoice."""

    def test_concurrent_payments_cannot_overpay(self):
        """Two concurrent payments of 60 each against a 100-unit invoice.

        Expected: exactly one succeeds, the other either gets a balance
        error or a stale-data conflict.  Total amount_paid must never
        exceed grand_total.
        """
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        results = {"a": None, "b": None}

        def pay(label, amount):
            s = factory()
            try:
                try:
                    invoice_service.record_payment(
                        s, invoice_id,
                        amount=decimal.Decimal(str(amount)),
                        actor_user_id=su.id,
                    )
                    s.commit()
                    results[label] = "SUCCESS"
                except Exception:
                    s.rollback()
                    results[label] = "REJECTED"
            finally:
                s.close()

        # Both try to pay 60 against a 100-unit invoice.
        t1 = threading.Thread(target=pay, args=("a", 60))
        t2 = threading.Thread(target=pay, args=("b", 60))

        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        # At least one must be rejected (total would be 120 > 100).
        rejected = sum(1 for v in results.values() if v == "REJECTED")
        assert rejected >= 1, (
            f"Expected at least 1 REJECTED, got {rejected}. Results: {results}"
        )

        # Verify: amount_paid must never exceed grand_total.
        session_verify = factory()
        try:
            inv = session_verify.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            assert inv.amount_paid <= grand_total, (
                f"Overpayment detected: amount_paid={inv.amount_paid} > "
                f"grand_total={grand_total}"
            )
            assert inv.balance_due >= 0, (
                f"Negative balance detected: balance_due={inv.balance_due}"
            )
        finally:
            session_verify.close()

    def test_concurrent_full_payments_one_succeeds(self):
        """Two concurrent full payments (100 each) against a 100-unit invoice.

        Expected: exactly one succeeds, the other is rejected.
        """
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        results = {"a": None, "b": None}

        def pay(label):
            s = factory()
            try:
                try:
                    invoice_service.record_payment(
                        s, invoice_id,
                        amount=grand_total,
                        actor_user_id=su.id,
                    )
                    s.commit()
                    results[label] = "SUCCESS"
                except Exception:
                    s.rollback()
                    results[label] = "REJECTED"
            finally:
                s.close()

        t1 = threading.Thread(target=pay, args=("a",))
        t2 = threading.Thread(target=pay, args=("b",))

        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        succeeded = sum(1 for v in results.values() if v == "SUCCESS")
        rejected = sum(1 for v in results.values() if v == "REJECTED")
        assert succeeded == 1, (
            f"Expected exactly 1 SUCCESS, got {succeeded}. Results: {results}"
        )
        assert rejected == 1, (
            f"Expected exactly 1 REJECTED, got {rejected}. Results: {results}"
        )

        # Verify final state.
        session_verify = factory()
        try:
            inv = session_verify.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            assert inv.amount_paid == grand_total
            assert inv.balance_due == decimal.Decimal("0")
            assert inv.state == "PAID"
        finally:
            session_verify.close()


@requires_database
class TestPaymentIdempotency:
    """Duplicate payment scenarios."""

    def test_sequential_duplicate_payment_rejected(self):
        """Two sequential full payments against the same invoice.

        First succeeds, second must be rejected (state check: invoice is PAID).
        """
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        # First payment: succeeds.
        s1 = factory()
        try:
            inv = invoice_service.record_payment(
                s1, invoice_id,
                amount=grand_total,
                actor_user_id=su.id,
            )
            s1.commit()
            s1.refresh(inv)
            assert inv.state == "PAID"
            assert inv.amount_paid == grand_total
        finally:
            s1.close()

        # Second payment: must be rejected (invoice is already PAID).
        s2 = factory()
        try:
            with pytest.raises(invoice_service.InvalidInvoiceStateTransitionError):
                invoice_service.record_payment(
                    s2, invoice_id,
                    amount=grand_total,
                    actor_user_id=su.id,
                )
            s2.rollback()
        finally:
            s2.close()

        # Verify no financial mutation from the second attempt.
        session_verify = factory()
        try:
            inv = session_verify.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            assert inv.amount_paid == grand_total
            assert inv.balance_due == decimal.Decimal("0")
            assert inv.state == "PAID"

            # Verify exactly one InvoiceHistory transition to PAID.
            history_count = session_verify.execute(
                select(func.count(InvoiceHistory.id)).where(
                    InvoiceHistory.invoice_id == invoice_id,
                    InvoiceHistory.to_state == "PAID",
                )
            ).scalar()
            assert history_count == 1, (
                f"Expected 1 PAID transition, got {history_count}"
            )
        finally:
            session_verify.close()

    def test_payment_exceeding_balance_rejected(self):
        """Payment amount exceeding remaining balance must be rejected."""
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        # Pay 280 first (partial — remaining = 20).
        s1 = factory()
        try:
            invoice_service.record_payment(
                s1, invoice_id,
                amount=decimal.Decimal("280"),
                actor_user_id=su.id,
            )
            s1.commit()
        finally:
            s1.close()

        # Try to pay 30 (exceeds remaining 20).
        s2 = factory()
        try:
            with pytest.raises(invoice_service.PaymentExceedsBalanceError):
                invoice_service.record_payment(
                    s2, invoice_id,
                    amount=decimal.Decimal("30"),
                    actor_user_id=su.id,
                )
            s2.rollback()
        finally:
            s2.close()

        # Verify no partial mutation.
        session_verify = factory()
        try:
            inv = session_verify.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            assert inv.amount_paid == decimal.Decimal("280")
            assert inv.balance_due == decimal.Decimal("20")
            assert inv.state == "PARTIALLY_PAID"
        finally:
            session_verify.close()

    def test_two_payments_exceeding_total_rejected(self):
        """Two sequential partial payments whose sum exceeds grand_total.

        First partial succeeds, second must be rejected.
        """
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        # First: pay 250 (partial — remaining = 50).
        s1 = factory()
        try:
            invoice_service.record_payment(
                s1, invoice_id,
                amount=decimal.Decimal("250"),
                actor_user_id=su.id,
            )
            s1.commit()
        finally:
            s1.close()

        # Second: pay 60 again (exceeds remaining 50).
        s2 = factory()
        try:
            with pytest.raises(invoice_service.PaymentExceedsBalanceError):
                invoice_service.record_payment(
                    s2, invoice_id,
                    amount=decimal.Decimal("60"),
                    actor_user_id=su.id,
                )
            s2.rollback()
        finally:
            s2.close()

        # Verify correct state.
        session_verify = factory()
        try:
            inv = session_verify.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            assert inv.amount_paid == decimal.Decimal("250")
            assert inv.balance_due == decimal.Decimal("50")
            assert inv.state == "PARTIALLY_PAID"
        finally:
            session_verify.close()

    def test_failed_payment_leaves_no_partial_mutation(self):
        """A payment that fails (e.g. exceeds balance) must leave zero
        side effects — no partial amount_paid changes, no history rows."""
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        # Record initial state.
        session_check = factory()
        try:
            inv_before = session_check.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            history_before = session_check.execute(
                select(func.count(InvoiceHistory.id)).where(
                    InvoiceHistory.invoice_id == invoice_id
                )
            ).scalar()
        finally:
            session_check.close()

        # Attempt payment that exceeds balance.
        s = factory()
        try:
            with pytest.raises(invoice_service.PaymentExceedsBalanceError):
                invoice_service.record_payment(
                    s, invoice_id,
                    amount=grand_total + decimal.Decimal("1"),
                    actor_user_id=su.id,
                )
            s.rollback()
        finally:
            s.close()

        # Verify zero side effects.
        session_verify = factory()
        try:
            inv_after = session_verify.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            ).scalar_one()
            history_after = session_verify.execute(
                select(func.count(InvoiceHistory.id)).where(
                    InvoiceHistory.invoice_id == invoice_id
                )
            ).scalar()

            assert inv_after.amount_paid == inv_before.amount_paid, (
                "amount_paid was mutated by a failed payment"
            )
            assert inv_after.balance_due == inv_before.balance_due, (
                "balance_due was mutated by a failed payment"
            )
            assert inv_after.state == inv_before.state, (
                f"state was mutated: {inv_before.state} -> {inv_after.state}"
            )
            assert history_after == history_before, (
                f"History rows added by failed payment: {history_before} -> {history_after}"
            )
        finally:
            session_verify.close()


@requires_database
class TestPaymentAtomicity:
    """Verify transaction atomicity of payment operations."""

    def test_payment_and_state_transition_are_atomic(self):
        """Payment amount and state transition must commit together."""
        factory = get_session_factory()

        session_setup = factory()
        try:
            session_setup, su = _setup(session_setup)
            rep = _create_rep(session_setup, su)
            product = _create_product(session_setup, su)
            order, customer, currency = _create_invoiced_order(session_setup, su, rep, product, qty=3, unit_price="100.0000")
            invoice = _create_invoice(session_setup, su, order)
            invoice_id = invoice.id
            grand_total = invoice.grand_total
            session_setup.commit()
        finally:
            session_setup.close()

        # Full payment.
        s = factory()
        try:
            inv = invoice_service.record_payment(
                s, invoice_id,
                amount=grand_total,
                actor_user_id=su.id,
            )
            s.commit()

            # After commit, verify all changes are persisted together.
            s2 = factory()
            try:
                inv_check = s2.execute(
                    select(Invoice).where(Invoice.id == invoice_id)
                ).scalar_one()
                assert inv_check.amount_paid == grand_total
                assert inv_check.balance_due == decimal.Decimal("0")
                assert inv_check.state == "PAID"
                assert inv_check.closed_at is not None
            finally:
                s2.close()
        finally:
            s.close()
