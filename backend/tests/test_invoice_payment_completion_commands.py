"""Focused tests for the 3 new Invoice → Payment → Completion bot commands:
/issue-invoice, /mark-paid, /mark-completed.

Covers per acceptance criteria:
- Authorized success
- Missing BOT_WRITE permission
- Representative scope denial (cross-rep)
- Invalid input / nonexistent resource
- Wrong state for the operation

All tests use the real PostgreSQL database.
"""

from __future__ import annotations

import decimal
import os
import uuid

import pytest
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.bot_platform_ref import BotPlatformRef
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.invoice import Invoice
from database.models.invoice_order import InvoiceOrder
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service
from services import bot_session_service
from services.bot_command_service import (
    BOT_QUERY_PERMISSION,
    BOT_WRITE_PERMISSION,
    BotMessage,
    BotResponse,
    COMMAND_REGISTRY,
    PermissionDeniedError,
    UnboundSessionError,
    process_message,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping invoice/payment/completion command tests",
)


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_new_write_commands.py)
# ---------------------------------------------------------------------------

def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def _ensure_telegram_platform(session: Session) -> BotPlatformRef:
    from sqlalchemy import select
    existing = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    system_user = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=system_user.id, updated_by=system_user.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session: Session, system_user) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-IPC-{suffix.upper()}",
        person_name=f"IPC Test Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session: Session, system_user, rep: Representative) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    user = auth_service.create_user(
        session,
        username=f"ipc_user_{suffix}",
        email=f"ipc_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )
    return user


def _grant_bot_query(session: Session, app_user: AppUser, system_user) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BQN_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BQN Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session, code=BOT_QUERY_PERMISSION,
            name="Query via bot", resource="bot", action="query",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session, role_code=role_code, permission_code=BOT_QUERY_PERMISSION,
    )
    rbac_service.assign_role(
        session, user_id=app_user.id, role_code=role_code,
        assigned_by=system_user.id,
    )


def _grant_bot_write(session: Session, app_user: AppUser, system_user) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BWN_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BWN Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session, code=BOT_WRITE_PERMISSION,
            name="Write via bot", resource="bot", action="write",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session, role_code=role_code, permission_code=BOT_WRITE_PERMISSION,
    )
    rbac_service.assign_role(
        session, user_id=app_user.id, role_code=role_code,
        assigned_by=system_user.id,
    )


def _make_bound_session(session: Session, system_user, *, platform_user_id: str):
    """Create rep + user + BOT_QUERY + BOT_WRITE + bound session."""
    rep = _create_representative(session, system_user)
    app_user = _create_app_user(session, system_user, rep)
    _grant_bot_query(session, app_user, system_user)
    _grant_bot_write(session, app_user, system_user)
    _ensure_telegram_platform(session)

    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=system_user.id,
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=app_user.id,
    )
    return rep, app_user, bot_session


def _make_bound_session_no_write(session: Session, system_user, *, platform_user_id: str):
    """Create rep + user + BOT_QUERY only (no BOT_WRITE) + bound session."""
    rep = _create_representative(session, system_user)
    app_user = _create_app_user(session, system_user, rep)
    _grant_bot_query(session, app_user, system_user)
    _ensure_telegram_platform(session)

    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=system_user.id,
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=app_user.id,
    )
    return rep, app_user, bot_session


def _assign_customer(session, rep_id, customer_id, *, actor_id):
    from datetime import datetime, timezone, timedelta
    assignment = CustomerRepAssignment(
        customer_id=customer_id,
        representative_id=rep_id,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        priority=1,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()


def _create_customer(session, system_user, suffix: str) -> Customer:
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    customer = Customer(
        code=f"CUST-IPC-{suffix}",
        name=f"IPC Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()
    return customer


def _make_order_fixtures(session, system_user):
    """Create all FK targets needed for an order."""
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-IPC-{suffix}",
        name="IPC Test Product",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_list = PriceList(
        name=f"IPC PL {suffix}",
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
        unit_price=decimal.Decimal("50.0000"),
        effective_from=_now(),
        created_by=system_user.id,
    )
    session.add(price_history)
    session.flush()

    return currency, warehouse, product, price_history


def _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, price_history, *, state="DRAFT"):
    """Create an order for the given representative in the specified state."""
    from services.order_service import create_order, OrderLineInput
    from services import order_service

    price_list = session.get(PriceList, price_history.price_list_id)

    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal("100"),
        unit_cost=decimal.Decimal("25.0000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()

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
                price_history_id=price_history.id,
                qty_ordered=2,
                fulfillment_mode="REP_LOCAL",
            ),
        ],
        created_by=system_user.id,
    )

    # Transition to the requested state.
    if state == "PENDING_APPROVAL":
        order_service.submit_order(session, order.id, actor_user_id=system_user.id)
    elif state == "APPROVED":
        order_service.submit_order(session, order.id, actor_user_id=system_user.id)
        order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    elif state == "RESERVED":
        order_service.submit_order(session, order.id, actor_user_id=system_user.id)
        order_service.approve_order(session, order.id, actor_user_id=system_user.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    elif state == "SHIPPED":
        order_service.submit_order(session, order.id, actor_user_id=system_user.id)
        order_service.approve_order(session, order.id, actor_user_id=system_user.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
        order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)
        order_lines = list(order_service.list_order_lines(session, order.id))
        for ol in order_lines:
            order_service.ship_order(
                session, order.id,
                shipments=[order_service.ShipmentInput(order_line_id=ol.id, quantity=ol.qty_ordered)],
                actor_user_id=system_user.id,
            )
    session.flush()
    return order


def _create_draft_invoice(session, system_user, order):
    """Create a DRAFT invoice from a SHIPPED order (does NOT issue it)."""
    from services import invoice_service
    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    session.flush()
    return invoice


def _create_issued_invoice(session, system_user, order):
    """Create a DRAFT invoice and issue it (order transitions SHIPPED → INVOICED)."""
    from services import invoice_service, customer_ledger_service
    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    invoice_service.issue_invoice(
        session, invoice.id, actor_user_id=system_user.id,
        record_entry=customer_ledger_service.record_entry,
    )
    session.flush()
    return invoice


def _pay_invoice_fully(session, system_user, invoice):
    """Fully pay an issued invoice via invoice_service.record_payment."""
    from services import invoice_service
    amount = decimal.Decimal(str(invoice.balance_due))
    invoice = invoice_service.record_payment(
        session, invoice.id,
        amount=amount,
        actor_user_id=system_user.id,
        note="Test full payment",
    )
    session.flush()
    return invoice


# ===========================================================================
# /issue-invoice command
# ===========================================================================


@requires_database
class TestIssueInvoiceCommand:
    """Tests for the /issue-invoice bot command."""

    def test_issue_invoice_transitions_to_issued(self):
        """A DRAFT invoice should be issued successfully."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ii-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_draft_invoice(session, system_user, order)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/issue-invoice {invoice.invoice_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "issued" in response.text.lower()
            assert invoice.invoice_number in response.text
            # Verify order transitioned to INVOICED
            session.refresh(order)
            assert order.state == "INVOICED"
        finally:
            session.close()

    def test_issue_invoice_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ii-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/issue-invoice")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_issue_invoice_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ii-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/issue-invoice INV-00000000-NONEXIST")
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_issue_invoice_wrong_state(self):
        """An already-issued invoice cannot be issued again."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ii-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_issued_invoice(session, system_user, order)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/issue-invoice {invoice.invoice_number}")
            response = process_message(session, message=msg)
            assert "cannot be issued" in response.text
        finally:
            session.close()

    def test_issue_invoice_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ii-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/issue-invoice INV-00000000-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_issue_invoice_cross_rep_isolation(self):
        """Rep B must not be able to issue Rep A's invoice."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"iia-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="SHIPPED")
            invoice_a = _create_draft_invoice(session, system_user, order_a)

            puid_b = f"iib-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/issue-invoice {invoice_a.invoice_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text or "does not belong" in response_b.text
        finally:
            session.close()


# ===========================================================================
# /mark-paid command
# ===========================================================================


@requires_database
class TestMarkPaidCommand:
    """Tests for the /mark-paid bot command."""

    def test_mark_paid_transitions_to_paid(self):
        """An INVOICED order should be marked as paid."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_issued_invoice(session, system_user, order)

            # Verify order is now INVOICED (issue_invoice coordinates this)
            session.refresh(order)
            assert order.state == "INVOICED"

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/mark-paid {order.order_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "paid" in response.text.lower()
            assert order.order_number in response.text
            # Verify order transitioned to PAID
            session.refresh(order)
            assert order.state == "PAID"
        finally:
            session.close()

    def test_mark_paid_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/mark-paid")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_mark_paid_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/mark-paid ORD-00000000-NONEXIST")
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_mark_paid_wrong_state(self):
        """A SHIPPED order cannot be marked as paid (must be INVOICED first)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/mark-paid {order.order_number}")
            response = process_message(session, message=msg)
            assert "cannot be marked as paid" in response.text
        finally:
            session.close()

    def test_mark_paid_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mp-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/mark-paid ORD-00000000-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_mark_paid_cross_rep_isolation(self):
        """Rep B must not be able to mark-paid Rep A's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"mpa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="SHIPPED")
            _create_issued_invoice(session, system_user, order_a)

            puid_b = f"mpb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/mark-paid {order_a.order_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text
        finally:
            session.close()


# ===========================================================================
# /mark-completed command
# ===========================================================================


@requires_database
class TestMarkCompletedCommand:
    """Tests for the /mark-completed bot command."""

    def test_mark_completed_transitions_to_completed(self):
        """A PAID order should be marked as completed."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")

            # Invoice it (SHIPPED → INVOICED)
            invoice = _create_issued_invoice(session, system_user, order)
            session.refresh(order)
            assert order.state == "INVOICED"

            # Pay the invoice (INVOICED → PAID on order)
            from services import order_service
            order_service.mark_paid(session, order.id, actor_user_id=system_user.id)
            session.refresh(order)
            assert order.state == "PAID"

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/mark-completed {order.order_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "completed" in response.text.lower()
            assert order.order_number in response.text
            # Verify order transitioned to COMPLETED
            session.refresh(order)
            assert order.state == "COMPLETED"
        finally:
            session.close()

    def test_mark_completed_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mc-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/mark-completed")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_mark_completed_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mc-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/mark-completed ORD-00000000-NONEXIST")
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_mark_completed_wrong_state(self):
        """An INVOICED order cannot be completed (must be PAID first)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            _create_issued_invoice(session, system_user, order)
            session.refresh(order)
            assert order.state == "INVOICED"

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/mark-completed {order.order_number}")
            response = process_message(session, message=msg)
            assert "cannot be completed" in response.text
        finally:
            session.close()

    def test_mark_completed_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"mc-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/mark-completed ORD-00000000-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_mark_completed_cross_rep_isolation(self):
        """Rep B must not be able to complete Rep A's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"mca-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="SHIPPED")
            _create_issued_invoice(session, system_user, order_a)
            from services import order_service
            order_service.mark_paid(session, order_a.id, actor_user_id=system_user.id)

            puid_b = f"mcb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/mark-completed {order_a.order_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text
        finally:
            session.close()
