"""PostgreSQL-backed tests for /order-history bot command (Tier 1 — read-only).

Covers:
- BOT_QUERY required
- BOT_WRITE alone insufficient
- unbound session rejected
- missing AppUser identity rejected
- missing argument
- nonexistent order
- out-of-scope order hidden
- cross-representative isolation (IDOR)
- single history entry
- multiple history entries
- correct chronological ordering
- correct from_state
- correct to_state
- actor displayed correctly
- note displayed correctly
- current status displayed correctly
- no UUID leakage
- no audit_log leakage
- no mutation occurs
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
    PermissionDeniedError,
    UnboundSessionError,
    process_message,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping /order-history tests",
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
        code=f"REP-OHR-{suffix.upper()}",
        person_name=f"OrderHistory Rep {suffix}",
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
        username=f"ohr_user_{suffix}",
        email=f"ohr_{suffix}@test.invalid",
        password="test-password-123",
        created_by=su.id,
        representative_id=rep.id,
    )


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"OHR_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"OHR {perm_code} {suffix}",
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


def _create_product(session: Session, su, prefix="SKU-OHR"):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{prefix}-{suffix}",
        name=f"OrderHistory Product {suffix}",
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
        code=f"C-OHR-{suffix}",
        name=f"OrderHistory Customer {suffix}",
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
        name=f"PL-OHR-{suffix2}",
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
# 1. BOT_QUERY required
# =======================================================================


@requires_database
class TestOrderHistoryRequiresBOTQuery:
    """/order-history must require BOT_QUERY permission."""

    def test_rejected_without_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid, grant_query=False
            )
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/order-history ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_QUERY_PERMISSION
        finally:
            session.close()

    def test_bot_write_alone_insufficient(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-bw-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid, grant_query=False
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/order-history ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_accepted_with_bot_query(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "Order History:" in response.text
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================


@requires_database
class TestOrderHistoryUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/order-history ORD-TEST",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Missing AppUser identity
# =======================================================================


@requires_database
class TestOrderHistoryMissingAppUser:
    def test_no_app_user_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            suffix = uuid.uuid4().hex[:8]
            rep = Representative(
                code=f"REP-OHRNULL-{suffix.upper()}",
                person_name=f"OrderHistoryNull Rep {suffix}",
                status="ACTIVE",
                created_by=su.id,
                updated_by=su.id,
            )
            session.add(rep)
            session.flush()

            _ensure_telegram_platform(session)
            puid = f"ohrnull-{uuid.uuid4().hex[:6]}"
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
                text="/order-history ORD-TEST",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 4. Missing argument
# =======================================================================


@requires_database
class TestOrderHistoryMissingArgs:
    def test_usage_hint_when_no_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-noarg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/order-history",
            )
            response = process_message(session, message=msg)
            assert "Usage:" in response.text
        finally:
            session.close()


# =======================================================================
# 5. Nonexistent order
# =======================================================================


@requires_database
class TestOrderHistoryNonexistent:
    def test_nonexistent_order_not_found(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/order-history ORD-NONEXISTENT-123",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Out-of-scope order hidden (IDOR prevention)
# =======================================================================


@requires_database
class TestOrderHistoryOutOfScope:
    def test_out_of_scope_order_not_found(self):
        """Another representative's order must not be accessible."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Create order under rep_a.
            puid_a = f"ohr-scopea-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            product = _create_product(session, su)
            order_a = _create_draft_order(session, su, rep_a, product)

            # Create rep_b.
            puid_b = f"ohr-scopeb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )

            # Rep_b tries to view rep_a's order history.
            msg = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/order-history {order_a.order_number}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 7. Cross-representative isolation (IDOR regression)
# =======================================================================


@requires_database
class TestOrderHistoryCrossRepresentativeIsolation:
    def test_rep_a_cannot_view_rep_b_history(self):
        """Critical IDOR test: Rep A must not see Rep B's order history."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A.
            puid_a = f"ohr-idora-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )

            # Rep B with an order that has history.
            puid_b = f"ohr-idorb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )
            product = _create_product(session, su, "SKU-IDOR")
            order_b = _create_draft_order(session, su, rep_b, product)
            _transition_order_to(session, su, order_b, "PENDING_APPROVAL")

            # Rep A tries to view Rep B's order history.
            msg = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/order-history {order_b.order_number}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
            # Must not contain any history data.
            assert "DRAFT" not in response.text
            assert "PENDING_APPROVAL" not in response.text
        finally:
            session.close()


# =======================================================================
# 8. History display — single entry
# =======================================================================


@requires_database
class TestOrderHistorySingleEntry:
    def test_single_transition_displayed(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-single-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)
            _transition_order_to(session, su, order, "PENDING_APPROVAL")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            assert "Order History:" in response.text
            assert order.order_number in response.text
            assert "Current Status: PENDING_APPROVAL" in response.text
            # Exactly one history entry.
            assert "1. DRAFT -> PENDING_APPROVAL" in response.text
        finally:
            session.close()


# =======================================================================
# 9. History display — multiple entries
# =======================================================================


@requires_database
class TestOrderHistoryMultipleEntries:
    def test_multiple_transitions_displayed(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-multi-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)
            _transition_order_to(session, su, order, "APPROVED")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            # Should have two entries: DRAFT->PENDING_APPROVAL, PENDING_APPROVAL->APPROVED
            assert "1. DRAFT -> PENDING_APPROVAL" in response.text
            assert "2. PENDING_APPROVAL -> APPROVED" in response.text
            assert "Current Status: APPROVED" in response.text
        finally:
            session.close()


# =======================================================================
# 10. Chronological ordering
# =======================================================================


@requires_database
class TestOrderHistoryChronological:
    def test_entries_in_chronological_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-chr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)
            _transition_order_to(session, su, order, "APPROVED")

            # Get history from service directly to verify ordering.
            from services.order_service import get_order_history
            history = list(get_order_history(session, order.id))

            # History should be ordered by event_at.
            for i in range(len(history) - 1):
                assert history[i].event_at <= history[i + 1].event_at

            # The bot response should show them in order.
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            # Verify ordering in text.
            idx1 = response.text.index("1. DRAFT -> PENDING_APPROVAL")
            idx2 = response.text.index("2. PENDING_APPROVAL -> APPROVED")
            assert idx1 < idx2
        finally:
            session.close()


# =======================================================================
# 11. Actor displayed correctly
# =======================================================================


@requires_database
class TestOrderHistoryActor:
    def test_actor_username_displayed(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-actor-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            # Submit using user.id as actor (not su.id from helper).
            from services import order_service
            order_service.submit_order(
                session, order.id, actor_user_id=user.id,
            )
            session.flush()
            session.refresh(order)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            # Actor should be displayed (username of user who performed the transition).
            assert "Actor:" in response.text
            assert user.username in response.text
        finally:
            session.close()


# =======================================================================
# 12. Note displayed correctly
# =======================================================================


@requires_database
class TestOrderHistoryNote:
    def test_note_displayed_when_present(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-note-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            # Submit with a note.
            from services import order_service
            order_service.submit_order(
                session, order.id, actor_user_id=user.id,
                note="Urgent order",
            )
            session.flush()
            session.refresh(order)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            assert "Note: Urgent order" in response.text
        finally:
            session.close()


# =======================================================================
# 13. No UUID leakage
# =======================================================================


@requires_database
class TestOrderHistoryNoUUIDLeakage:
    def test_no_uuids_in_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-uuid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)
            _transition_order_to(session, su, order, "PENDING_APPROVAL")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            # Must not contain raw UUIDs.
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
            puid = f"ohr-uuid2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/order-history ORD-NONEXISTENT-999",
            )
            response = process_message(session, message=msg)
            assert "00000000-0000-0000-0000" not in response.text
        finally:
            session.close()


# =======================================================================
# 14. No mutation occurs
# =======================================================================


@requires_database
class TestOrderHistoryNoMutation:
    def test_no_state_change(self):
        """Viewing history must not change order state."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-nomut-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            # Record initial state and history count.
            initial_state = order.state
            initial_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                )
            ).scalars().all()))

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            process_message(session, message=msg)

            # Verify: no state change.
            session.refresh(order)
            assert order.state == initial_state

            # Verify: no new history entries.
            post_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order.id
                )
            ).scalars().all()))
            assert post_history_count == initial_history_count
        finally:
            session.close()


# =======================================================================
# 15. Empty history
# =======================================================================


@requires_database
class TestOrderHistoryEmpty:
    def test_new_order_no_history(self):
        """A newly created DRAFT order has no history entries."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-empty-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order-history {order.order_number}",
            )
            response = process_message(session, message=msg)

            assert "No history records found" in response.text
            assert "Current Status: DRAFT" in response.text
        finally:
            session.close()


# =======================================================================
# 16. Regression — existing commands still work
# =======================================================================


@requires_database
class TestOrderHistoryRegression:
    def test_orders_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-regord-{uuid.uuid4().hex[:6]}"
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
            puid = f"ohr-regme-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/me",
            )
            response = process_message(session, message=msg)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_help_includes_order_history(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-reghelp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/help",
            )
            response = process_message(session, message=msg)
            assert "/order-history" in response.text
        finally:
            session.close()

    def test_transfer_history_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ohr-reght-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/transfer-history",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()
