"""PostgreSQL-backed tests for /submit bot command (Tier 2 — direct write).

Covers:
- BOT_WRITE required
- BOT_QUERY alone insufficient
- unbound session rejected
- missing AppUser identity rejected
- missing argument
- malformed order number (nonexistent)
- nonexistent order
- out-of-scope order hidden
- cross-representative isolation
- DRAFT → PENDING_APPROVAL succeeds
- resulting state is PENDING_APPROVAL
- order history contains DRAFT → PENDING_APPROVAL
- actor is recorded correctly
- repeated submission rejected
- APPROVED order rejected
- RESERVED order rejected
- FULFILLING order rejected
- SHIPPED order rejected
- INVOICED order rejected
- PAID order rejected
- COMPLETED order rejected
- CANCELLED order rejected
- no UUID leakage
- regression for existing order commands

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
    reason="DATABASE_URL not set; skipping /submit tests",
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
        code=f"REP-SUB-{suffix.upper()}",
        person_name=f"Submit Rep {suffix}",
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
        username=f"sub_user_{suffix}",
        email=f"sub_{suffix}@test.invalid",
        password="test-password-123",
        created_by=su.id,
        representative_id=rep.id,
    )


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"SUB_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"SUB {perm_code} {suffix}",
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


def _create_product(session: Session, su, prefix="SKU-SUB"):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{prefix}-{suffix}",
        name=f"Submit Product {suffix}",
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


def _create_draft_order(session, su, rep, product, *, qty=10):
    """Create a DRAFT order belonging to the given representative."""
    currency = bootstrap_service.ensure_default_currency(
        session, actor_id=su.id
    )
    warehouse = bootstrap_service.ensure_default_warehouse(
        session, actor_id=su.id
    )

    # Create customer and assign to representative.
    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-SUB-{suffix}",
        name=f"Submit Customer {suffix}",
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

    # Create price.
    suffix2 = uuid.uuid4().hex[:8]
    price_list = PriceList(
        name=f"PL-SUB-{suffix2}",
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


def _transition_order_to(session, su, order, target_state):
    """Transition an order through the state machine to the target state.

    Uses the canonical order_service functions. Seeds stock as needed
    for reservation/fulfillment states. Returns the order.
    """
    from services import order_service
    from database.models.order_line import OrderLine

    # Ensure stock is available for reservation-dependent states.
    if target_state in ("RESERVED", "FULFILLING", "SHIPPED", "INVOICED", "PAID", "COMPLETED"):
        lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == order.id)
        ).scalars().all()
        for line in lines:
            _seed_stock(session, line.fulfillment_warehouse_id, line.product_id, 100, su)

    if target_state == "PENDING_APPROVAL":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
    elif target_state == "APPROVED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.approve_order(session, order.id, actor_user_id=su.id)
    elif target_state == "RESERVED":
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
    elif target_state == "PAID":
        _transition_order_to(session, su, order, "INVOICED")
        order_service.mark_paid(session, order.id, actor_user_id=su.id)
    elif target_state == "COMPLETED":
        _transition_order_to(session, su, order, "PAID")
        order_service.mark_completed(session, order.id, actor_user_id=su.id)
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
class TestSubmitRequiresBOTWrite:
    """/submit must require BOT_WRITE permission."""

    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/submit ORD-TEST",
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
            puid = f"sub-bq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/submit ORD-TEST",
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
            puid = f"sub-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "submitted for approval" in response.text
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================


@requires_database
class TestSubmitUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/submit ORD-TEST",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Missing AppUser identity
# =======================================================================


@requires_database
class TestSubmitMissingAppUser:
    def test_no_app_user_rejected(self):
        """A representative without a linked AppUser must be denied."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            suffix = uuid.uuid4().hex[:8]
            rep = Representative(
                code=f"REP-SUBNULL-{suffix.upper()}",
                person_name=f"SubmitNull Rep {suffix}",
                status="ACTIVE",
                created_by=su.id,
                updated_by=su.id,
            )
            session.add(rep)
            session.flush()

            _ensure_telegram_platform(session)
            puid = f"subnull-{uuid.uuid4().hex[:6]}"
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
                text="/submit ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 4. Missing argument
# =======================================================================


@requires_database
class TestSubmitMissingArgs:
    def test_usage_hint_when_no_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-noarg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/submit",
            )
            response = process_message(session, message=msg)
            assert "Usage:" in response.text
        finally:
            session.close()


# =======================================================================
# 5. Nonexistent order
# =======================================================================


@requires_database
class TestSubmitNonexistentOrder:
    def test_nonexistent_order_not_found(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/submit ORD-NONEXISTENT-123",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Out-of-scope order hidden (IDOR prevention)
# =======================================================================


@requires_database
class TestSubmitOutOfScope:
    def test_out_of_scope_order_not_found(self):
        """Another representative's order must not be accessible."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Create order under rep_a.
            puid_a = f"sub-scopea-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            _grant_bot_write(session, user_a, su)
            product = _create_product(session, su)
            order_a = _create_draft_order(session, su, rep_a, product)

            # Create rep_b with BOT_WRITE.
            puid_b = f"sub-scopeb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )
            _grant_bot_write(session, user_b, su)

            # Rep_b tries to submit rep_a's order.
            msg = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/submit {order_a.order_number}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
            # Verify order state unchanged.
            session.refresh(order_a)
            assert order_a.state == "DRAFT"
        finally:
            session.close()


# =======================================================================
# 7. Cross-representative isolation (IDOR regression)
# =======================================================================


@requires_database
class TestSubmitCrossRepresentativeIsolation:
    def test_rep_a_cannot_submit_rep_b_order(self):
        """Critical IDOR test: Rep A must not be able to submit Rep B's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A.
            puid_a = f"sub-idora-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            _grant_bot_write(session, user_a, su)

            # Rep B with their own order.
            puid_b = f"sub-idorb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )
            _grant_bot_write(session, user_b, su)
            product = _create_product(session, su, "SKU-IDOR")
            order_b = _create_draft_order(session, su, rep_b, product)

            # Record initial state.
            initial_state = order_b.state
            initial_history_count = session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order_b.id
                )
            ).scalars().all()

            # Rep A tries to submit Rep B's order.
            msg = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/submit {order_b.order_number}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()

            # Verify: order state unchanged.
            session.refresh(order_b)
            assert order_b.state == initial_state

            # Verify: no history entry created.
            post_history = session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order_b.id
                )
            ).scalars().all()
            assert len(post_history) == len(initial_history_count)
        finally:
            session.close()


# =======================================================================
# 8. DRAFT → PENDING_APPROVAL succeeds
# =======================================================================


@requires_database
class TestSubmitDraftSuccess:
    def test_draft_order_submitted(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-ok2-{uuid.uuid4().hex[:6]}"
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

            # Verify state.
            session.refresh(order)
            assert order.state == "PENDING_APPROVAL"
        finally:
            session.close()

    def test_resulting_state_is_pending_approval(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-state-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert "PENDING_APPROVAL" in response.text
        finally:
            session.close()


# =======================================================================
# 9. Order history contains DRAFT → PENDING_APPROVAL
# =======================================================================


@requires_database
class TestSubmitHistoryRecorded:
    def test_history_records_transition(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-hist-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            process_message(session, message=msg)

            # Verify order_status_history.
            history = list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                ).order_by(OrderStatusHistory.event_at)
            ).scalars().all())

            # create_order does NOT write a history row (no from_state),
            # so submit is the first history entry.
            assert len(history) >= 1
            last = history[-1]
            assert last.from_state == "DRAFT"
            assert last.to_state == "PENDING_APPROVAL"
        finally:
            session.close()

    def test_actor_recorded_correctly(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-actor-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
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
# 10. Repeated submission rejected
# =======================================================================


@requires_database
class TestSubmitRepeated:
    def test_already_submitted_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-rep-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            # First submit — should succeed.
            msg1 = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response1 = process_message(session, message=msg1)
            assert "submitted for approval" in response1.text

            # Second submit — should fail (already PENDING_APPROVAL).
            msg2 = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response2 = process_message(session, message=msg2)
            # The validator catches non-DRAFT state and returns the error directly
            # (not wrapped in "Error:").
            assert "cannot be submitted" in response2.text.lower()
        finally:
            session.close()


# =======================================================================
# 11. Non-DRAFT states rejected
# =======================================================================


@requires_database
class TestSubmitNonDraftStates:
    """Submit must reject orders that are not in DRAFT state."""

    def _test_rejects_state(self, target_state):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-{target_state[:4].lower()}-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su, f"SKU-{target_state[:6]}")
            order = _create_draft_order(session, su, rep, product)
            _transition_order_to(session, su, order, target_state)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert target_state in response.text
            assert "cannot be submitted" in response.text.lower()
        finally:
            session.close()

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


# =======================================================================
# 12. No UUID leakage
# =======================================================================


@requires_database
class TestSubmitNoUUIDLeakage:
    def test_no_uuids_in_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-uuid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/submit {order.order_number}",
            )
            response = process_message(session, message=msg)

            # Must not contain raw UUIDs.
            assert str(order.id) not in response.text
            assert str(rep.id) not in response.text
            assert str(user.id) not in response.text
        finally:
            session.close()

    def test_not_found_response_no_uuid_leakage(self):
        """Even 'not found' responses must not leak UUIDs."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-uuid2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/submit ORD-NONEXISTENT-999",
            )
            response = process_message(session, message=msg)
            # Should not contain any UUID pattern.
            assert "00000000-0000-0000-0000" not in response.text
        finally:
            session.close()


# =======================================================================
# 13. Regression — existing commands still work
# =======================================================================


@requires_database
class TestSubmitRegression:
    def test_orders_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-regord-{uuid.uuid4().hex[:6]}"
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
            puid = f"sub-regme-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/me",
            )
            response = process_message(session, message=msg)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_dispatch_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-regdsp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/dispatch",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_help_includes_submit(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sub-reghelp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/help",
            )
            response = process_message(session, message=msg)
            assert "/submit" in response.text
        finally:
            session.close()
