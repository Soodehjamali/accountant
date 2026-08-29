"""Focused tests for the 4 new write bot commands:
/approve-order, /reserve-stock, /create-invoice, /record-payment.

Covers per acceptance criteria:
- Authorized success
- Missing BOT_WRITE permission
- Representative scope denial (cross-rep)
- Invalid input / nonexistent resource
- Wrong state for the operation
- Approval flow when required (record-payment)

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
from database.models.invoice_line import InvoiceLine
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
from services.approval_service import get_pending_request

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping new write command tests",
)


# ---------------------------------------------------------------------------
# Helpers
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
        code=f"REP-NWC-{suffix.upper()}",
        person_name=f"NWC Test Rep {suffix}",
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
        username=f"nwc_user_{suffix}",
        email=f"nwc_{suffix}@test.invalid",
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


def _assign_warehouse(session, rep_id, warehouse_id, *, is_primary=False, actor_id):
    from datetime import datetime, timezone, timedelta
    assignment = WarehouseAssignment(
        representative_id=rep_id,
        warehouse_id=warehouse_id,
        is_primary=is_primary,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()


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
        # Ship the order
        order_lines = list(order_service.list_order_lines(session, order.id))
        for ol in order_lines:
            order_service.ship_order(
                session, order.id,
                shipments=[order_service.ShipmentInput(order_line_id=ol.id, quantity=ol.qty_ordered)],
                actor_user_id=system_user.id,
            )
    session.flush()
    return order


def _make_order_fixtures(session, system_user):
    """Create all FK targets needed for an order."""
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-NWC-{suffix}",
        name="NWC Test Product",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_list = PriceList(
        name=f"NWC PL {suffix}",
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


def _create_customer(session, system_user, suffix: str) -> Customer:
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    customer = Customer(
        code=f"CUST-NWC-{suffix}",
        name=f"NWC Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()
    return customer


def _create_invoice_for_order(session, system_user, order):
    """Create a DRAFT invoice from a SHIPPED order and issue it."""
    from services import invoice_service

    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    invoice_service.issue_invoice(
        session, invoice.id, actor_user_id=system_user.id,
    )
    session.flush()
    return invoice


# ===========================================================================
# /approve-order command
# ===========================================================================


@requires_database
class TestApproveOrderCommand:
    """Tests for the /approve-order bot command."""

    def test_approve_order_transitions_to_approved(self):
        """A PENDING_APPROVAL order should be approved."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ao-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="PENDING_APPROVAL")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/approve-order {order.order_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "approved" in response.text.lower()
            assert order.order_number in response.text
        finally:
            session.close()

    def test_approve_order_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ao-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/approve-order")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_approve_order_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ao-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/approve-order ORD-00000000-NONEXIST")
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_approve_order_wrong_state(self):
        """A DRAFT order cannot be approved."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ao-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="DRAFT")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/approve-order {order.order_number}")
            response = process_message(session, message=msg)
            assert "cannot be approved" in response.text
        finally:
            session.close()

    def test_approve_order_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ao-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/approve-order ORD-00000000-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_approve_order_cross_rep_isolation(self):
        """Rep B must not be able to approve Rep A's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Rep A with a PENDING_APPROVAL order
            puid_a = f"aoa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="PENDING_APPROVAL")

            # Rep B
            puid_b = f"aob-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/approve-order {order_a.order_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text or "Access denied" in response_b.text
        finally:
            session.close()


# ===========================================================================
# /reserve-stock command
# ===========================================================================


@requires_database
class TestReserveStockCommand:
    """Tests for the /reserve-stock bot command."""

    def test_reserve_stock_transitions_to_reserved(self):
        """An APPROVED order with sufficient stock should become RESERVED."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rs-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="APPROVED")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/reserve-stock {order.order_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert order.order_number in response.text
            # Should be RESERVED or BACKORDERED depending on stock
            assert "RESERVED" in response.text or "BACKORDERED" in response.text
        finally:
            session.close()

    def test_reserve_stock_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rs-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/reserve-stock")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_reserve_stock_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rs-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/reserve-stock ORD-00000000-NONEXIST")
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_reserve_stock_wrong_state(self):
        """A DRAFT order cannot reserve stock."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rs-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="DRAFT")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/reserve-stock {order.order_number}")
            response = process_message(session, message=msg)
            assert "cannot reserve" in response.text
        finally:
            session.close()

    def test_reserve_stock_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rs-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/reserve-stock ORD-00000000-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_reserve_stock_cross_rep_isolation(self):
        """Rep B must not be able to reserve stock for Rep A's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"rsa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="APPROVED")

            puid_b = f"rsb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/reserve-stock {order_a.order_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text
        finally:
            session.close()


# ===========================================================================
# /create-invoice command
# ===========================================================================


@requires_database
class TestCreateInvoiceCommand:
    """Tests for the /create-invoice bot command."""

    def test_create_invoice_creates_draft_invoice(self):
        """A SHIPPED order should produce a DRAFT invoice."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ci-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/create-invoice {order.order_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "INV-" in response.text
            assert "DRAFT" in response.text
            assert order.order_number in response.text
        finally:
            session.close()

    def test_create_invoice_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ci-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-invoice")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_create_invoice_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ci-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-invoice ORD-00000000-NONEXIST")
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_create_invoice_wrong_state(self):
        """A DRAFT order cannot be invoiced."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ci-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="DRAFT")

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/create-invoice {order.order_number}")
            response = process_message(session, message=msg)
            assert "cannot create an invoice" in response.text
        finally:
            session.close()

    def test_create_invoice_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ci-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-invoice ORD-00000000-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_create_invoice_cross_rep_isolation(self):
        """Rep B must not be able to invoice Rep A's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"cia-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="SHIPPED")

            puid_b = f"cib-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/create-invoice {order_a.order_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text
        finally:
            session.close()


# ===========================================================================
# /record-payment command
# ===========================================================================


@requires_database
class TestRecordPaymentCommand:
    """Tests for the /record-payment bot command."""

    def test_record_payment_creates_approval_request(self):
        """record-payment requires approval — should create a PENDING request."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_invoice_for_order(session, system_user, order)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/record-payment {invoice.invoice_number} 50 BANK_TRANSFER REF-TEST",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "submitted for approval" in response.text.lower()
        finally:
            session.close()

    def test_record_payment_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/record-payment")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_record_payment_nonexistent_invoice(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/record-payment INV-00000000-NONEXIST 1000 BANK_TRANSFER",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text
        finally:
            session.close()

    def test_record_payment_invalid_amount(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_invoice_for_order(session, system_user, order)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/record-payment {invoice.invoice_number} -500 BANK_TRANSFER",
            )
            response = process_message(session, message=msg)
            assert "positive" in response.text.lower()
        finally:
            session.close()

    def test_record_payment_invalid_method(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_invoice_for_order(session, system_user, order)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/record-payment {invoice.invoice_number} 1000 CRYPTOCURRENCY",
            )
            response = process_message(session, message=msg)
            assert "Invalid payment method" in response.text
        finally:
            session.close()

    def test_record_payment_amount_exceeds_balance(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph, state="SHIPPED")
            invoice = _create_invoice_for_order(session, system_user, order)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/record-payment {invoice.invoice_number} 999999999 BANK_TRANSFER",
            )
            response = process_message(session, message=msg)
            assert "exceeds" in response.text.lower()
        finally:
            session.close()

    def test_record_payment_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rp-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, system_user, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/record-payment INV-00000000-TEST 1000 BANK_TRANSFER",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_record_payment_cross_rep_isolation(self):
        """Rep B must not be able to record payment for Rep A's invoice."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"rpa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph, state="SHIPPED")
            invoice_a = _create_invoice_for_order(session, system_user, order_a)

            puid_b = f"rpb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/record-payment {invoice_a.invoice_number} 1000 BANK_TRANSFER",
            )
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text or "does not belong" in response_b.text
        finally:
            session.close()
