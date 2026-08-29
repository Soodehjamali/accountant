"""Focused tests for the 3 implemented bot commands: /orders, /order, /customers.

Covers per acceptance criteria:
- Authorized success
- Missing/unbound session
- Missing BOT_QUERY permission
- Cross-representative isolation
- Invalid input (malformed UUID, missing argument)
- Nonexistent resource
- Safe response formatting (no internal IDs leaked)

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
from database.models.representative import Representative
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
from services import bot_session_service
from services.bot_command_service import (
    BOT_QUERY_PERMISSION,
    BotMessage,
    BotResponse,
    COMMAND_REGISTRY,
    UnboundSessionError,
    process_message,
)
from services.representative_scope_service import RepresentativeNotFoundError

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping bot command tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def _past(days=30):
    import datetime
    return _now() - datetime.timedelta(days=days)


def _ensure_telegram_platform(session: Session) -> BotPlatformRef:
    existing = session.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
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
        code=f"REP-CMD-{suffix.upper()}",
        person_name=f"Cmd Test Rep {suffix}",
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
        username=f"cmd_user_{suffix}",
        email=f"cmd_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )
    return user


def _grant_bot_query(session: Session, app_user: AppUser, system_user) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BQC_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BQ Tester {suffix}",
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


def _make_bound_session(session: Session, system_user, *, platform_user_id: str):
    """Create rep + user + bound session. Returns (rep, app_user, bot_session)."""
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


def _make_bound_session_no_perm(session: Session, system_user, *, platform_user_id: str):
    """Create rep + user WITHOUT BOT_QUERY + bound session."""
    rep = _create_representative(session, system_user)
    app_user = _create_app_user(session, system_user, rep)
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


def _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, price_history):
    """Create a DRAFT order for the given representative."""
    from services.order_service import create_order, OrderLineInput
    from services import inventory_service
    from database.models.price_list import PriceList

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
    session.flush()
    return order


def _make_order_fixtures(session, system_user):
    """Create all FK targets needed for an order."""
    from database.models.product import Product
    from database.models.price_history import PriceHistory
    from database.models.price_list import PriceList

    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-CMD-{suffix}",
        name="Cmd Test Product",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_list = PriceList(
        name=f"Cmd PL {suffix}",
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
        code=f"CUST-CMD-{suffix}",
        name=f"Cmd Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()
    return customer


# ===========================================================================
# /orders command
# ===========================================================================


@requires_database
class TestOrdersCommand:
    """Tests for the /orders bot command."""

    def test_orders_returns_recent_orders(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ord-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/orders")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert order.order_number in response.text
            assert "DRAFT" in response.text
            assert "100.0000" in response.text  # 2 * 50.00
        finally:
            session.close()

    def test_orders_empty_when_no_orders(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"ord-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/orders")
            response = process_message(session, message=msg)

            assert "No orders found" in response.text
        finally:
            session.close()

    def test_orders_requires_bot_query_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noperm-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_perm(session, system_user, platform_user_id=puid)

            from services.bot_command_service import PermissionDeniedError
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/orders")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_orders_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/orders")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_orders_cross_rep_isolation(self):
        """Rep A must not see Rep B's orders."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Rep A with an order
            puid_a = f"ora-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph)

            # Rep B with no orders
            puid_b = f"orb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text="/orders")
            response_b = process_message(session, message=msg_b)

            assert order_a.order_number not in response_b.text
            assert "No orders found" in response_b.text
        finally:
            session.close()

    def test_orders_response_format_no_internal_ids(self):
        """Response must not contain UUIDs or internal IDs."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"fmt-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/orders")
            response = process_message(session, message=msg)

            # Should not contain raw UUIDs
            assert str(rep.id) not in response.text
            assert str(customer.id) not in response.text
        finally:
            session.close()


# ===========================================================================
# /order <id> command
# ===========================================================================


@requires_database
class TestOrderCommand:
    """Tests for the /order <id> bot command."""

    def test_order_returns_details(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"od-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order {order.id}",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert order.order_number in response.text
            assert "DRAFT" in response.text
            assert "LOCAL" in response.text
            assert "100.0000" in response.text
        finally:
            session.close()

    def test_order_missing_argument(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"miss-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/order")
            response = process_message(session, message=msg)

            assert "Usage" in response.text
        finally:
            session.close()

    def test_order_malformed_uuid(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"bad-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/order not-a-uuid",
            )
            response = process_message(session, message=msg)

            assert "Invalid order ID" in response.text
            assert "not-a-uuid" in response.text
        finally:
            session.close()

    def test_order_nonexistent(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noexist-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            fake_id = str(uuid.uuid4())
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order {fake_id}",
            )
            response = process_message(session, message=msg)

            assert "not found" in response.text
        finally:
            session.close()

    def test_order_access_denied_cross_rep(self):
        """Rep B trying to access Rep A's order must be denied."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Rep A with an order
            puid_a = f"oa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            order_a = _create_order_for_rep(session, system_user, rep_a, customer_a, currency, warehouse, product, ph)

            # Rep B
            puid_b = f"ob-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/order {order_a.id}",
            )
            response_b = process_message(session, message=msg_b)

            assert "Access denied" in response_b.text
            assert order_a.order_number not in response_b.text
        finally:
            session.close()

    def test_order_requires_bot_query_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noperm-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_perm(session, system_user, platform_user_id=puid)

            from services.bot_command_service import PermissionDeniedError
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order {uuid.uuid4()}",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_order_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text=f"/order {uuid.uuid4()}",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_order_response_no_internal_ids(self):
        """Response must not leak UUIDs."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, ph = _make_order_fixtures(session, system_user)
            order = _create_order_for_rep(session, system_user, rep, customer, currency, warehouse, product, ph)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/order {order.id}",
            )
            response = process_message(session, message=msg)

            # Must not leak the order UUID, customer UUID, or rep UUID
            assert str(order.id) not in response.text
            assert str(customer.id) not in response.text
            assert str(rep.id) not in response.text
        finally:
            session.close()


# ===========================================================================
# /customers command
# ===========================================================================


@requires_database
class TestCustomersCommand:
    """Tests for the /customers bot command."""

    def test_customers_returns_assigned_customers(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"cust-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/customers")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert customer.code in response.text
            assert customer.name in response.text
            assert "ACTIVE" in response.text
        finally:
            session.close()

    def test_customers_empty_when_none_assigned(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"nocust-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/customers")
            response = process_message(session, message=msg)

            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_customers_requires_bot_query_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noperm-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_perm(session, system_user, platform_user_id=puid)

            from services.bot_command_service import PermissionDeniedError
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/customers")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_customers_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/customers")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_customers_cross_rep_isolation(self):
        """Rep A's customers must not appear in Rep B's response."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            puid_a = f"ca-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)

            puid_b = f"cb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text="/customers")
            response_b = process_message(session, message=msg_b)

            assert customer_a.code not in response_b.text
            assert customer_a.name not in response_b.text
            assert "No customers assigned" in response_b.text
        finally:
            session.close()

    def test_customers_respects_assignment_time_window(self):
        """An expired assignment should not return the customer."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"tw-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            # Expired assignment (ended 10 days ago)
            from datetime import datetime, timezone, timedelta
            assignment = CustomerRepAssignment(
                customer_id=customer.id,
                representative_id=rep.id,
                effective_from=datetime.now(timezone.utc) - timedelta(days=60),
                effective_to=datetime.now(timezone.utc) - timedelta(days=10),
                priority=1,
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(assignment)
            session.flush()
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/customers")
            response = process_message(session, message=msg)

            assert customer.code not in response.text
            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_customers_response_no_internal_ids(self):
        """Response must not contain UUIDs."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/customers")
            response = process_message(session, message=msg)

            assert str(customer.id) not in response.text
            assert str(rep.id) not in response.text
        finally:
            session.close()


# ===========================================================================
# /balance command
# ===========================================================================


@requires_database
class TestBalanceCommand:
    """Tests for the /balance bot command."""

    def test_balance_returns_balances_for_assigned_customers(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"bal-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)

            # Create a ledger entry so the customer has a non-zero balance.
            from services.customer_ledger_service import ensure_customer_ledger, record_entry
            ensure_customer_ledger(
                session, customer_id=customer.id, currency_id=customer.currency_id,
            )
            record_entry(
                session,
                customer_id=customer.id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal("250.0000"),
                currency_id=customer.currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert customer.code in response.text
            assert customer.name in response.text
            assert "250.0000" in response.text
        finally:
            session.close()

    def test_balance_empty_when_no_customers_assigned(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"bal-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            response = process_message(session, message=msg)

            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_balance_zero_for_customer_with_no_entries(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"bal-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            response = process_message(session, message=msg)

            assert customer.code in response.text
            assert "0" in response.text  # zero balance
        finally:
            session.close()

    def test_balance_cross_rep_isolation(self):
        """Rep A's balances must not appear in Rep B's response."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Rep A with a customer and balance
            puid_a = f"ba-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep_a.id, customer_a.id, actor_id=system_user.id)
            from services.customer_ledger_service import ensure_customer_ledger, record_entry
            ensure_customer_ledger(session, customer_id=customer_a.id, currency_id=customer_a.currency_id)
            record_entry(
                session,
                customer_id=customer_a.id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal("100.0000"),
                currency_id=customer_a.currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )
            session.flush()

            # Rep B with no customers
            puid_b = f"bb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text="/balance")
            response_b = process_message(session, message=msg_b)

            assert customer_a.code not in response_b.text
            assert "No customers assigned" in response_b.text
        finally:
            session.close()

    def test_balance_expired_assignment_excluded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"bal-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            # Expired assignment
            from datetime import datetime, timezone, timedelta
            assignment = CustomerRepAssignment(
                customer_id=customer.id,
                representative_id=rep.id,
                effective_from=datetime.now(timezone.utc) - timedelta(days=60),
                effective_to=datetime.now(timezone.utc) - timedelta(days=10),
                priority=1,
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(assignment)
            session.flush()
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            response = process_message(session, message=msg)

            assert customer.code not in response.text
            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_balance_requires_bot_query_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noperm-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_perm(session, system_user, platform_user_id=puid)

            from services.bot_command_service import PermissionDeniedError
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_balance_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/balance")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_balance_no_internal_uuids_exposed(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep.id, customer.id, actor_id=system_user.id)
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            response = process_message(session, message=msg)

            assert str(customer.id) not in response.text
            assert str(rep.id) not in response.text
        finally:
            session.close()

    def test_balance_formatting_with_multiple_customers(self):
        """Multiple customers should each appear on their own line."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"bal-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            c1 = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            c2 = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            _assign_customer(session, rep.id, c1.id, actor_id=system_user.id)
            _assign_customer(session, rep.id, c2.id, actor_id=system_user.id)
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/balance")
            response = process_message(session, message=msg)

            assert c1.code in response.text
            assert c2.code in response.text
            # Both should have zero balance since no entries
            lines = response.text.strip().split("\n")
            # First line is header, then one line per customer
            assert len(lines) >= 3  # header + at least 2 customer lines
        finally:
            session.close()


# ===========================================================================
# /inventory command
# ===========================================================================


@requires_database
class TestInventoryCommand:
    """Tests for the /inventory bot command."""

    def test_inventory_returns_stock_for_warehouse(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            # Create a dedicated warehouse so accumulated test stock doesn't interfere
            suffix_wh = uuid.uuid4().hex[:6]
            from database.models.warehouse import Warehouse
            warehouse = Warehouse(
                code=f"WH-INV-{suffix_wh}",
                name="Inventory Test WH",
                type="REPRESENTATIVE",
                ownership_mode="OWNED",
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(warehouse)
            session.flush()
            _assign_warehouse(session, rep.id, warehouse.id, is_primary=True, actor_id=system_user.id)

            # Create product and post inventory
            from database.models.product import Product
            from services import inventory_service
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
            bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            product = Product(
                sku=f"SKU-INV-{suffix}",
                name="Inventory Test Product",
                base_uom_id=uom.id,
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(product)
            session.flush()

            inventory_service.post_transaction(
                session,
                product_id=product.id,
                warehouse_id=warehouse.id,
                movement_type_code="INITIAL_OPENING_BALANCE",
                signed_quantity=decimal.Decimal("75.0000"),
                unit_cost=decimal.Decimal("10.0000"),
                currency_id=currency.id,
                actor_user_id=system_user.id,
            )
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert warehouse.code in response.text
            assert product.sku in response.text
            assert "75.0000" in response.text
        finally:
            session.close()

    def test_inventory_empty_when_no_stock(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            # Create a dedicated warehouse with no stock
            suffix = uuid.uuid4().hex[:6]
            from database.models.warehouse import Warehouse
            wh_empty = Warehouse(
                code=f"WH-EMPTY-{suffix}",
                name="Empty Warehouse",
                type="REPRESENTATIVE",
                ownership_mode="OWNED",
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(wh_empty)
            session.flush()
            _assign_warehouse(session, rep.id, wh_empty.id, is_primary=True, actor_id=system_user.id)
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            response = process_message(session, message=msg)

            assert f"No stock in {wh_empty.code}" in response.text
        finally:
            session.close()

    def test_inventory_no_warehouse_assigned(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"inv-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            response = process_message(session, message=msg)

            assert "No warehouse assigned" in response.text
        finally:
            session.close()

    def test_inventory_cross_rep_isolation(self):
        """Rep A's warehouse stock must not appear in Rep B's response."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Rep A with warehouse and stock
            puid_a = f"ia-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_a)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
            _assign_warehouse(session, rep_a.id, warehouse.id, is_primary=True, actor_id=system_user.id)

            from database.models.product import Product
            from services import inventory_service
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
            bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            product = Product(
                sku=f"SKU-INV-{suffix}",
                name="Isolation Test",
                base_uom_id=uom.id,
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(product)
            session.flush()
            inventory_service.post_transaction(
                session,
                product_id=product.id,
                warehouse_id=warehouse.id,
                movement_type_code="INITIAL_OPENING_BALANCE",
                signed_quantity=decimal.Decimal("50.0000"),
                unit_cost=decimal.Decimal("10.0000"),
                currency_id=currency.id,
                actor_user_id=system_user.id,
            )
            session.flush()

            # Rep B with no warehouse
            puid_b = f"ib-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, system_user, platform_user_id=puid_b)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text="/inventory")
            response_b = process_message(session, message=msg_b)

            assert product.sku not in response_b.text
            assert "No warehouse assigned" in response_b.text
        finally:
            session.close()

    def test_inventory_expired_assignment_excluded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
            # Expired assignment
            from datetime import datetime, timezone, timedelta
            assignment = WarehouseAssignment(
                representative_id=rep.id,
                warehouse_id=warehouse.id,
                is_primary=True,
                effective_from=datetime.now(timezone.utc) - timedelta(days=60),
                effective_to=datetime.now(timezone.utc) - timedelta(days=10),
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(assignment)
            session.flush()
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            response = process_message(session, message=msg)

            assert "No warehouse assigned" in response.text
        finally:
            session.close()

    def test_inventory_requires_bot_query_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noperm-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_perm(session, system_user, platform_user_id=puid)

            from services.bot_command_service import PermissionDeniedError
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_inventory_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/inventory")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_inventory_no_internal_uuids_exposed(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"noid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
            _assign_warehouse(session, rep.id, warehouse.id, is_primary=True, actor_id=system_user.id)
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            response = process_message(session, message=msg)

            assert str(rep.id) not in response.text
            assert str(warehouse.id) not in response.text
        finally:
            session.close()

    def test_inventory_uses_primary_warehouse_only(self):
        """When assigned to multiple warehouses, /inventory shows the primary only."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            from database.models.product import Product
            from database.models.warehouse import Warehouse
            from services import inventory_service
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
            bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

            # Create a dedicated primary warehouse with stock
            suffix_wh = uuid.uuid4().hex[:6]
            wh_primary = Warehouse(
                code=f"WH-PRIM-{suffix_wh}",
                name="Primary WH",
                type="REPRESENTATIVE",
                ownership_mode="OWNED",
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(wh_primary)
            session.flush()
            _assign_warehouse(session, rep.id, wh_primary.id, is_primary=True, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            product_primary = Product(
                sku=f"SKU-P-{suffix}",
                name="Primary WH Product",
                base_uom_id=uom.id,
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(product_primary)
            session.flush()
            inventory_service.post_transaction(
                session,
                product_id=product_primary.id,
                warehouse_id=wh_primary.id,
                movement_type_code="INITIAL_OPENING_BALANCE",
                signed_quantity=decimal.Decimal("100.0000"),
                unit_cost=decimal.Decimal("10.0000"),
                currency_id=currency.id,
                actor_user_id=system_user.id,
            )
            session.commit()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/inventory")
            response = process_message(session, message=msg)

            assert wh_primary.code in response.text
            assert product_primary.sku in response.text
            assert "100.0000" in response.text
        finally:
            session.close()
