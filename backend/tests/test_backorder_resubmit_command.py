"""PostgreSQL-backed tests for /backorder-resubmit bot command (Tier 2 — direct write).

Covers:
- BOT_WRITE required
- BOT_QUERY alone insufficient
- unbound session rejected
- missing AppUser identity rejected
- missing argument
- nonexistent order
- out-of-scope order hidden
- cross-representative isolation (IDOR)
- BACKORDERED → PENDING_APPROVAL succeeds
- resulting state is PENDING_APPROVAL
- correct history entry (from_state, to_state)
- actor recorded correctly
- audit entry recorded through existing mechanism
- repeated resubmission follows domain behavior
- non-BACKORDERED states rejected (DRAFT, PENDING_APPROVAL, APPROVED,
  RESERVED, FULFILLING, SHIPPED, INVOICED, PAID, COMPLETED, CANCELLED,
  RETURNED, PARTIALLY_FULFILLED)
- no UUID leakage
- no unrelated order mutation
- domain side effects match resubmit_order()
- regression against existing order commands

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
from database.models.audit_log import AuditLog
from database.models.bot_platform_ref import BotPlatformRef
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.order import Order
from database.models.order_status_history import OrderStatusHistory
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
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
    reason="DATABASE_URL not set; skipping /backorder-resubmit tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_telegram_platform(session: Session) -> BotPlatformRef:
    existing = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    su = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=su.id, updated_by=su.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session: Session, su) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-RES-{suffix.upper()}",
        person_name=f"BackorderResubmit Rep {suffix}",
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session: Session, su, rep: Representative) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(
        session,
        username=f"res_user_{suffix}",
        email=f"res_{suffix}@test.invalid",
        password="test-password-123",
        created_by=su.id,
        representative_id=rep.id,
    )


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"RES_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"RES {perm_code} {suffix}",
        created_by=su.id,
    )
    try:
        rbac_service.create_permission(
            session, code=perm_code, name=f"Permission {perm_code}",
            resource="bot", action=perm_code.lower(), created_by=su.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session, role_code=role_code, permission_code=perm_code,
    )
    rbac_service.assign_role(
        session, user_id=app_user.id, role_code=role_code,
        assigned_by=su.id,
    )


def _grant_bot_query(session, app_user, su):
    _grant_permission(session, app_user, su, BOT_QUERY_PERMISSION)


def _grant_bot_write(session, app_user, su):
    _grant_permission(session, app_user, su, BOT_WRITE_PERMISSION)


def _make_bound_session(session, su, *, platform_user_id, grant_query=True):
    rep = _create_representative(session, su)
    user = _create_app_user(session, su, rep)
    if grant_query:
        _grant_bot_query(session, user, su)
    _ensure_telegram_platform(session)
    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=su.id,
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=user.id,
    )
    return rep, user, bot_session


def _create_product(session: Session, su, prefix="SKU-RES"):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{prefix}-{suffix}",
        name=f"BackorderResubmit Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(
            session, actor_id=su.id
        ).id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(product)
    session.flush()
    return product


def _seed_stock(session, wh_id, product_id, qty, su):
    """Seed inventory for stock reservation tests."""
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


def _seed_stock_for_order(session, su, order):
    """Seed stock for all lines of an order."""
    from database.models.order_line import OrderLine
    lines = session.execute(
        select(OrderLine).where(OrderLine.order_id == order.id)
    ).scalars().all()
    for line in lines:
        _seed_stock(session, line.fulfillment_warehouse_id, line.product_id, 100, su)


def _create_draft_order(session, su, rep, product, *, qty=10):
    """Create a DRAFT order belonging to the given representative."""
    currency = bootstrap_service.ensure_default_currency(
        session, actor_id=su.id
    )
    warehouse = bootstrap_service.ensure_default_warehouse(
        session, actor_id=su.id
    )

    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-RES-{suffix}",
        name=f"BackorderResubmit Customer {suffix}",
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
        name=f"PL-RES-{suffix2}",
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


def _create_backordered_order(session, su, rep, product, *, qty=10):
    """Create an order in BACKORDERED state.

    The path: DRAFT → PENDING_APPROVAL → APPROVED → BACKORDERED
    (insufficient stock triggers BACKORDERED instead of RESERVED).
    """
    from services import order_service
    order = _create_draft_order(session, su, rep, product, qty=qty)
    order_service.submit_order(session, order.id, actor_user_id=su.id)
    order_service.approve_order(session, order.id, actor_user_id=su.id)
    # Reserve without sufficient stock → goes to BACKORDERED.
    order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
    session.flush()
    session.refresh(order)
    assert order.state == "BACKORDERED", (
        f"Setup failed: expected BACKORDERED, got {order.state}"
    )
    return order


def _transition_order_to(session, su, order, target_state):
    """Transition an order through the state machine to the target state."""
    from services import order_service
    from database.models.order_line import OrderLine

    if target_state in ("RESERVED", "FULFILLING", "PARTIALLY_FULFILLED",
                        "SHIPPED", "INVOICED", "PAID", "COMPLETED"):
        lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == order.id)
        ).scalars().all()
        for line in lines:
            _seed_stock(session, line.fulfillment_warehouse_id,
                        line.product_id, 100, su)

    if target_state == "PENDING_APPROVAL":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
    elif target_state == "APPROVED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.approve_order(session, order.id, actor_user_id=su.id)
    elif target_state == "RESERVED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.approve_order(session, order.id, actor_user_id=su.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
    elif target_state == "BACKORDERED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.approve_order(session, order.id, actor_user_id=su.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
    elif target_state == "FULFILLING":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.approve_order(session, order.id, actor_user_id=su.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
        order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
    elif target_state == "SHIPPED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.approve_order(session, order.id, actor_user_id=su.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
        order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
        from services.order_service import ShipmentInput
        lines = list(order_service.list_order_lines(session, order.id))
        order_service.ship_order(
            session, order.id, actor_user_id=su.id,
            shipments=[ShipmentInput(
                order_line_id=lines[0].id,
                quantity=lines[0].qty_ordered,
            )],
        )
    elif target_state == "INVOICED":
        _transition_order_to(session, su, order, "SHIPPED")
        order_service.mark_invoiced(session, order.id, actor_user_id=su.id)
    elif target_state == "CANCELLED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.cancel_order(session, order.id, actor_user_id=su.id)
    session.flush()
    session.refresh(order)
    return order


# =======================================================================
# 1. BOT_WRITE required
# =======================================================================


@requires_database
class TestBackorderResubmitRequiresBOTWrite:
    """/backorder-resubmit must require BOT_WRITE permission."""

    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/backorder-resubmit ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
        finally:
            session.close()

    def test_bot_query_alone_insufficient(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-bq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/backorder-resubmit ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_accepted_with_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "resubmitted" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================


@requires_database
class TestBackorderResubmitUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/backorder-resubmit ORD-TEST",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Missing AppUser identity
# =======================================================================


@requires_database
class TestBackorderResubmitMissingAppUser:
    def test_no_app_user_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            suffix = uuid.uuid4().hex[:8]
            rep = Representative(
                code=f"REP-RESNULL-{suffix.upper()}",
                person_name=f"BackorderResubmitNull Rep {suffix}",
                status="ACTIVE",
                created_by=su.id,
                updated_by=su.id,
            )
            session.add(rep)
            session.flush()

            _ensure_telegram_platform(session)
            puid = f"resnull-{uuid.uuid4().hex[:6]}"
            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id,
                platform_code="TELEGRAM", created_by=su.id,
            )
            bot_session_service.create_binding(
                session, binding_token=token, platform_code="TELEGRAM",
                platform_user_id=puid, linked_by=su.id,
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/backorder-resubmit ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 4. Missing argument
# =======================================================================


@requires_database
class TestBackorderResubmitMissingArgs:
    def test_usage_hint_when_no_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-noarg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/backorder-resubmit",
            )
            response = process_message(session, message=msg)
            assert "Usage:" in response.text
        finally:
            session.close()


# =======================================================================
# 5. Nonexistent order
# =======================================================================


@requires_database
class TestBackorderResubmitNonexistent:
    def test_nonexistent_order_not_found(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/backorder-resubmit ORD-NONEXISTENT-123",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Out-of-scope order hidden (IDOR prevention)
# =======================================================================


@requires_database
class TestBackorderResubmitOutOfScope:
    def test_out_of_scope_order_not_found(self):
        """Another representative's order must not be accessible."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Create backordered order under rep_a.
            puid_a = f"res-scopea-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            _grant_bot_write(session, user_a, su)
            product = _create_product(session, su)
            order_a = _create_backordered_order(session, su, rep_a, product)

            # Create rep_b with BOT_WRITE.
            puid_b = f"res-scopeb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )
            _grant_bot_write(session, user_b, su)

            # Rep_b tries to resubmit rep_a's order.
            msg = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order_a.order_number}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
            # Verify order state unchanged.
            session.refresh(order_a)
            assert order_a.state == "BACKORDERED"
        finally:
            session.close()


# =======================================================================
# 7. Cross-representative isolation (IDOR regression)
# =======================================================================


@requires_database
class TestBackorderResubmitCrossRepresentativeIsolation:
    def test_rep_a_cannot_resubmit_rep_b_order(self):
        """Critical IDOR test."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A.
            puid_a = f"res-idora-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            _grant_bot_write(session, user_a, su)

            # Rep B with a backordered order.
            puid_b = f"res-idorb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )
            _grant_bot_write(session, user_b, su)
            product = _create_product(session, su, "SKU-IDOR")
            order_b = _create_backordered_order(session, su, rep_b, product)

            initial_state = order_b.state
            initial_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order_b.id
                )
            ).scalars().all()))

            # Rep A tries to resubmit Rep B's order.
            msg = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order_b.order_number}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()

            # Verify: order state unchanged.
            session.refresh(order_b)
            assert order_b.state == initial_state

            # Verify: no history entry created.
            post_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order_b.id
                )
            ).scalars().all()))
            assert post_history_count == initial_history_count
        finally:
            session.close()


# =======================================================================
# 8. BACKORDERED → PENDING_APPROVAL succeeds
# =======================================================================


@requires_database
class TestBackorderResubmitSuccess:
    def test_backordered_order_resubmitted(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-ok2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert "resubmitted" in response.text.lower()

            session.refresh(order)
            assert order.state == "PENDING_APPROVAL"
        finally:
            session.close()

    def test_resulting_state_is_pending_approval(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-state-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert "PENDING_APPROVAL" in response.text
        finally:
            session.close()


# =======================================================================
# 9. History entry with correct from_state → to_state
# =======================================================================


@requires_database
class TestBackorderResubmitHistory:
    def test_history_records_transition(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-hist-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            # Record history count before resubmit.
            pre_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                )
            ).scalars().all()))

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            process_message(session, message=msg)

            history = list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                ).order_by(OrderStatusHistory.event_at)
            ).scalars().all())

            # Exactly one new history entry added.
            assert len(history) == pre_count + 1
            last = history[-1]
            assert last.from_state == "BACKORDERED"
            assert last.to_state == "PENDING_APPROVAL"
        finally:
            session.close()

    def test_actor_recorded_correctly(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-actor-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            process_message(session, message=msg)

            history = list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                ).order_by(OrderStatusHistory.event_at)
            ).scalars().all())
            assert len(history) >= 1
            last = history[-1]
            assert last.actor_user_id == user.id
        finally:
            session.close()


# =======================================================================
# 10. Audit log recorded
# =======================================================================


@requires_database
class TestBackorderResubmitAudit:
    def test_audit_log_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-audit-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            process_message(session, message=msg)

            # The _transition() choke point writes an audit_log UPDATE.
            audit = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "order",
                    AuditLog.entity_id == order.id,
                    AuditLog.action == "UPDATE",
                )
            ).scalars().all()
            assert len(audit) >= 1
            last = audit[-1]
            assert last.actor_user_id == user.id
            assert "PENDING_APPROVAL" in str(last.after_json)
        finally:
            session.close()


# =======================================================================
# 11. Repeated resubmission follows domain behavior
# =======================================================================


@requires_database
class TestBackorderResubmitRepeated:
    def test_already_resubmitted_rejected(self):
        """After resubmit, order is PENDING_APPROVAL — resubmit again must fail."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-rep-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            # First resubmit — should succeed.
            msg1 = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response1 = process_message(session, message=msg1)
            assert "resubmitted" in response1.text.lower()

            # Second resubmit — should fail (already PENDING_APPROVAL).
            msg2 = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response2 = process_message(session, message=msg2)
            assert "cannot be resubmitted" in response2.text.lower()
        finally:
            session.close()


# =======================================================================
# 12. Non-BACKORDERED states rejected
# =======================================================================


@requires_database
class TestBackorderResubmitNonBackordered:
    """Resubmit must only accept BACKORDERED orders."""

    def _test_rejects_state(self, target_state):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-rej-{target_state[:4].lower()}-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su, f"SKU-REJ-{target_state[:4]}")
            order = _create_draft_order(session, su, rep, product)

            # Use order_service functions directly for reliable transitions.
            from services import order_service
            from services.order_service import ShipmentInput

            if target_state == "PENDING_APPROVAL":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
            elif target_state == "APPROVED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
            elif target_state == "RESERVED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
            elif target_state == "BACKORDERED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
            elif target_state == "FULFILLING":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
                order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
            elif target_state == "SHIPPED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
                order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
                lines = list(order_service.list_order_lines(session, order.id))
                order_service.ship_order(
                    session, order.id, actor_user_id=su.id,
                    shipments=[ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
                )
            elif target_state == "INVOICED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
                order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
                lines = list(order_service.list_order_lines(session, order.id))
                order_service.ship_order(
                    session, order.id, actor_user_id=su.id,
                    shipments=[ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
                )
                order_service.mark_invoiced(session, order.id, actor_user_id=su.id)
            elif target_state == "PAID":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
                order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
                lines = list(order_service.list_order_lines(session, order.id))
                order_service.ship_order(
                    session, order.id, actor_user_id=su.id,
                    shipments=[ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
                )
                order_service.mark_invoiced(session, order.id, actor_user_id=su.id)
                order_service.mark_paid(session, order.id, actor_user_id=su.id)
            elif target_state == "COMPLETED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
                order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
                lines = list(order_service.list_order_lines(session, order.id))
                order_service.ship_order(
                    session, order.id, actor_user_id=su.id,
                    shipments=[ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
                )
                order_service.mark_invoiced(session, order.id, actor_user_id=su.id)
                order_service.mark_paid(session, order.id, actor_user_id=su.id)
                order_service.mark_completed(session, order.id, actor_user_id=su.id)
            elif target_state == "CANCELLED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.cancel_order(session, order.id, actor_user_id=su.id)
            elif target_state == "RETURNED":
                order_service.submit_order(session, order.id, actor_user_id=su.id)
                order_service.approve_order(session, order.id, actor_user_id=su.id)
                _seed_stock_for_order(session, su, order)
                order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
                order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
                lines = list(order_service.list_order_lines(session, order.id))
                order_service.ship_order(
                    session, order.id, actor_user_id=su.id,
                    shipments=[ShipmentInput(order_line_id=lines[0].id, quantity=lines[0].qty_ordered)],
                )
                order_service.record_return(session, order.id, actor_user_id=su.id)

            session.flush()
            session.refresh(order)
            assert order.state == target_state, (
                f"Setup failed: expected {target_state}, got {order.state}"
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert "cannot be resubmitted" in response.text.lower()
            # Verify state unchanged.
            session.refresh(order)
            assert order.state == target_state
        finally:
            session.close()

    def test_rejected_when_draft(self):
        self._test_rejects_state("DRAFT")

    def test_rejected_when_pending_approval(self):
        self._test_rejects_state("PENDING_APPROVAL")

    def test_rejected_when_approved(self):
        self._test_rejects_state("APPROVED")

    def test_rejected_when_reserved(self):
        self._test_rejects_state("RESERVED")

    def test_rejected_when_fulfilling(self):
        self._test_rejects_state("FULFILLING")

    def test_rejected_when_shipped(self):
        self._test_rejects_state("SHIPPED")

    def test_rejected_when_invoiced(self):
        self._test_rejects_state("INVOICED")

    def test_rejected_when_paid(self):
        self._test_rejects_state("PAID")

    def test_rejected_when_completed(self):
        self._test_rejects_state("COMPLETED")

    def test_rejected_when_cancelled(self):
        self._test_rejects_state("CANCELLED")

    def test_rejected_when_returned(self):
        self._test_rejects_state("RETURNED")


# =======================================================================
# 13. No UUID leakage
# =======================================================================


@requires_database
class TestBackorderResubmitNoUUIDLeakage:
    def test_no_uuids_in_success_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-uuid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            response = process_message(session, message=msg)

            assert str(order.id) not in response.text
            assert str(rep.id) not in response.text
            assert str(user.id) not in response.text
        finally:
            session.close()

    def test_not_found_response_no_uuid_leakage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-uuid2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/backorder-resubmit ORD-NONEXISTENT-999",
            )
            response = process_message(session, message=msg)
            assert "00000000-0000-0000-0000" not in response.text
        finally:
            session.close()


# =======================================================================
# 14. No unrelated mutation
# =======================================================================


@requires_database
class TestBackorderResubmitNoUnrelatedMutation:
    def test_only_state_and_history_change(self):
        """Resubmit must only change state and add one history entry."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-nomut-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_backordered_order(session, su, rep, product)

            # Record initial state.
            initial_grand_total = order.grand_total
            initial_ordered_at = order.ordered_at
            initial_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                )
            ).scalars().all()))

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/backorder-resubmit {order.order_number}",
            )
            process_message(session, message=msg)

            session.refresh(order)
            # State changed.
            assert order.state == "PENDING_APPROVAL"
            # Financials unchanged.
            assert order.grand_total == initial_grand_total
            # Timestamps unchanged.
            assert order.ordered_at == initial_ordered_at
            # Exactly one new history entry.
            post_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                )
            ).scalars().all()))
            assert post_history_count == initial_history_count + 1
        finally:
            session.close()


# =======================================================================
# 15. Regression — existing commands still work
# =======================================================================


@requires_database
class TestBackorderResubmitRegression:
    def test_orders_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-regord-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/orders",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()

    def test_me_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-regme-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/me",
            )
            response = process_message(session, message=msg)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_submit_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-regsub-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert "submitted for approval" in response.text
        finally:
            session.close()

    def test_help_includes_backorder_resubmit(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"res-reghelp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/help",
            )
            response = process_message(session, message=msg)
            assert "/backorder-resubmit" in response.text
        finally:
            session.close()
