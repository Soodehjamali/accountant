"""PostgreSQL-backed tests for /ship bot command (Tier 2 — direct write).

Covers:
- Successful shipment from FULFILLING state
- Correct order/representative authorization
- Cross-representative isolation / IDOR
- Unknown order number
- Missing BOT_WRITE
- Invalid/missing arguments
- Unknown SKU
- Invalid quantity (zero, negative, non-numeric)
- SKU not belonging to the order
- Invalid order state (DRAFT, RESERVED, SHIPPED, CANCELLED)
- Repeated shipment (quantity exceeds remaining)

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
from database.models.order_line import OrderLine
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
    reason="DATABASE_URL not set; skipping /ship tests",
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
        code=f"REP-SHP-{suffix.upper()}",
        person_name=f"Ship Rep {suffix}",
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
        username=f"shp_user_{suffix}",
        email=f"shp_{suffix}@test.invalid",
        password="test-password-123",
        created_by=su.id,
        representative_id=rep.id,
    )


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"SHP_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"SHP {perm_code} {suffix}",
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


def _create_product(session: Session, su, prefix="SKU-SHP"):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{prefix}-{suffix}",
        name=f"Ship Product {suffix}",
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
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)

    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-SHP-{suffix}",
        name=f"Ship Customer {suffix}",
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
        name=f"PL-SHP-{suffix2}",
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


def _create_fulfilling_order(session, su, rep, product, *, qty=10):
    """Create an order in FULFILLING state."""
    from services import order_service

    order = _create_draft_order(session, su, rep, product, qty=qty)
    order_service.submit_order(session, order.id, actor_user_id=su.id)
    order_service.approve_order(session, order.id, actor_user_id=su.id)
    # Seed stock so reservation succeeds.
    lines = session.execute(
        select(OrderLine).where(OrderLine.order_id == order.id)
    ).scalars().all()
    for line in lines:
        _seed_stock(session, line.fulfillment_warehouse_id, line.product_id, 100, su)
    order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=su.id)
    session.flush()
    session.refresh(order)
    assert order.state == "FULFILLING"
    return order


def _transition_order_to(session, su, order, target_state):
    """Transition an order through the state machine to the target state."""
    from services import order_service
    from services.order_service import ShipmentInput

    if target_state in ("RESERVED", "FULFILLING", "PARTIALLY_FULFILLED",
                        "SHIPPED", "INVOICED", "PAID", "COMPLETED"):
        lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == order.id)
        ).scalars().all()
        for line in lines:
            _seed_stock(session, line.fulfillment_warehouse_id,
                        line.product_id, 100, su)

    if target_state == "RESERVED":
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
        lines = list(order_service.list_order_lines(session, order.id))
        order_service.ship_order(
            session, order.id, actor_user_id=su.id,
            shipments=[ShipmentInput(
                order_line_id=lines[0].id,
                quantity=lines[0].qty_ordered,
            )],
        )
    elif target_state == "CANCELLED":
        order_service.submit_order(session, order.id, actor_user_id=su.id)
        order_service.cancel_order(session, order.id, actor_user_id=su.id)
    session.flush()
    session.refresh(order)
    return order


# =======================================================================
# 1. Successful shipment
# =======================================================================


@requires_database
class TestShipSuccess:
    def test_ship_from_fulfilling(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "shipment recorded" in response.text.lower()

            session.refresh(order)
            assert order.state == "SHIPPED"
        finally:
            session.close()

    def test_resulting_state_is_shipped(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-state-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "SHIPPED" in response.text
        finally:
            session.close()


# =======================================================================
# 2. Cross-representative isolation / IDOR
# =======================================================================


@requires_database
class TestShipIDOR:
    def test_rep_a_cannot_ship_rep_b_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            puid_a = f"shp-idora-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            _grant_bot_write(session, user_a, su)

            puid_b = f"shp-idorb-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )
            _grant_bot_write(session, user_b, su)
            product = _create_product(session, su, "SKU-IDOR")
            order_b = _create_fulfilling_order(session, su, rep_b, product, qty=10)

            initial_state = order_b.state
            initial_history_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order_b.id
                )
            ).scalars().all()))

            msg = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/ship {order_b.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()

            session.refresh(order_b)
            assert order_b.state == initial_state
            post_count = len(list(session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == order_b.id
                )
            ).scalars().all()))
            assert post_count == initial_history_count
        finally:
            session.close()


# =======================================================================
# 3. Unknown order
# =======================================================================


@requires_database
class TestShipUnknownOrder:
    def test_unknown_order_not_found(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship ORD-NONEXISTENT-123 SKU-TEST 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 4. Missing BOT_WRITE
# =======================================================================


@requires_database
class TestShipPermission:
    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-noperm-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship ORD-TEST SKU-TEST 10",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
        finally:
            session.close()


# =======================================================================
# 5. Invalid/missing arguments
# =======================================================================


@requires_database
class TestShipArgs:
    def test_usage_hint_when_missing_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-noarg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship",
            )
            response = process_message(session, message=msg)
            assert "Usage:" in response.text
        finally:
            session.close()

    def test_usage_hint_when_two_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-2arg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship ORD-TEST SKU-TEST",
            )
            response = process_message(session, message=msg)
            assert "Usage:" in response.text
        finally:
            session.close()


# =======================================================================
# 6. Invalid quantity
# =======================================================================


@requires_database
class TestShipInvalidQuantity:
    def test_non_numeric_quantity(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-nan-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship ORD-TEST SKU-TEST abc",
            )
            response = process_message(session, message=msg)
            assert "invalid quantity" in response.text.lower()
        finally:
            session.close()

    def test_zero_quantity(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-zero-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship ORD-TEST SKU-TEST 0",
            )
            response = process_message(session, message=msg)
            assert "greater than zero" in response.text.lower()
        finally:
            session.close()

    def test_negative_quantity(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-neg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/ship ORD-TEST SKU-TEST -5",
            )
            response = process_message(session, message=msg)
            assert "greater than zero" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 7. Unknown SKU
# =======================================================================


@requires_database
class TestShipUnknownSku:
    def test_unknown_sku_not_found(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-nosku-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} NONEXISTENT-SKU 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 8. SKU not on order
# =======================================================================


@requires_database
class TestShipSkuNotOnOrder:
    def test_sku_not_on_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-wrong-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            other_product = _create_product(session, su, "SKU-OTHER")
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {other_product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "not on order" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 9. Invalid order state
# =======================================================================


@requires_database
class TestShipInvalidState:
    def test_rejected_when_reserved(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-res-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            _transition_order_to(session, su, order, "RESERVED")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "cannot be shipped" in response.text.lower()
        finally:
            session.close()

    def test_rejected_when_shipped(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-shp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            _transition_order_to(session, su, order, "SHIPPED")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "cannot be shipped" in response.text.lower()
        finally:
            session.close()

    def test_rejected_when_cancelled(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-canc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_draft_order(session, su, rep, product, qty=10)
            _transition_order_to(session, su, order, "CANCELLED")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "cannot be shipped" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 10. Repeated shipment / quantity exceeds remaining
# =======================================================================


@requires_database
class TestShipQuantityExceeds:
    def test_quantity_exceeds_remaining(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-exceed-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 15",
            )
            response = process_message(session, message=msg)
            assert "cannot ship" in response.text.lower() or "only" in response.text.lower()
            # Order should remain FULFILLING.
            session.refresh(order)
            assert order.state == "FULFILLING"
        finally:
            session.close()


# =======================================================================
# 11. No UUID leakage
# =======================================================================


@requires_database
class TestShipNoUUID:
    def test_no_uuids_in_success_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-uuid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 10",
            )
            response = process_message(session, message=msg)

            assert str(order.id) not in response.text
            assert str(rep.id) not in response.text
            assert str(user.id) not in response.text
        finally:
            session.close()

    def test_no_uuids_in_error_response(self):
        """Even error responses must not leak UUIDs."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-uuid2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_fulfilling_order(session, su, rep, product, qty=10)

            # Exceed quantity — should get a clean error.
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/ship {order.order_number} {product.sku} 100",
            )
            response = process_message(session, message=msg)
            assert "00000000-0000-0000-0000" not in response.text
        finally:
            session.close()


# =======================================================================
# 12. Regression
# =======================================================================


@requires_database
class TestShipRegression:
    def test_help_includes_ship(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-reghelp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/help",
            )
            response = process_message(session, message=msg)
            assert "/ship" in response.text
        finally:
            session.close()

    def test_start_fulfillment_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"shp-regsf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            # Create a RESERVED order.
            from services import order_service
            order = _create_draft_order(session, su, rep, product, qty=10)
            order_service.submit_order(session, order.id, actor_user_id=su.id)
            order_service.approve_order(session, order.id, actor_user_id=su.id)
            lines = session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalars().all()
            for line in lines:
                _seed_stock(session, line.fulfillment_warehouse_id, line.product_id, 100, su)
            order_service.reserve_order_stock(session, order.id, actor_user_id=su.id)
            session.refresh(order)
            assert order.state == "RESERVED"

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/start-fulfillment {order.order_number}",
            )
            response = process_message(session, message=msg)
            assert "fulfillment started" in response.text.lower()
        finally:
            session.close()
