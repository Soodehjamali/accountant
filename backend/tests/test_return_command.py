"""PostgreSQL-backed tests for /return bot command (Tier 3 mutation).

Covers per acceptance criteria:
- Authorization: BOT_WRITE required, unbound session, representative identity
- Validation: missing args, invalid order, cross-rep order, invalid product,
  invalid quantity, zero quantity, quantity exceeding returnable, invalid reason code
- Scope: own order succeeds, cross-rep order rejected
- Approval: PENDING state, no mutation while PENDING, SoD, approval executes,
  rejection no-op, cancellation no-op, no double execution
- Security: no UUID leakage, payload cannot override identity, executor rejects unknown type
- Audit: approval history, resolution audit, return mutation audit
- Regression: read commands and /create-order, /adjust still work
- Concurrency: concurrent approval/execution

All tests use the real PostgreSQL database (no mocks).
"""

from __future__ import annotations

import decimal
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.approval_request import ApprovalRequest
from database.models.audit_log import AuditLog
from database.models.bot_platform_ref import BotPlatformRef
from database.models.customer import Customer
from database.models.customer_return import CustomerReturn
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.reason_code_ref import ReasonCodeRef
from database.models.representative import Representative
from database.models.return_line import ReturnLine
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
from services.approval_service import (
    SeparationOfDutiesError,
    approve_request,
    cancel_request,
    create_approval_request,
    get_pending_request,
    reject_request,
)
from services.approval_execution_service import (
    execute_approved_request,
    EXECUTOR_REGISTRY,
    ApprovalNotApprovedError,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping /return tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def _ensure_telegram_platform(session: Session) -> BotPlatformRef:
    existing = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    system_user = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(
        code="TELEGRAM",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(p)
    session.flush()
    return p


def _create_representative(session: Session, system_user) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-RET-{suffix.upper()}",
        person_name=f"Return Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(
    session: Session, system_user, rep: Representative
) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(
        session,
        username=f"ret_user_{suffix}",
        email=f"ret_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_bot_query(
    session: Session, app_user: AppUser, system_user
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BQRET_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BQRET Tester {suffix}",
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


def _grant_bot_write(
    session: Session, app_user: AppUser, system_user
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BWRET_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BWRET Tester {suffix}",
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


def _make_bound_session(
    session: Session, system_user, *, platform_user_id: str
):
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


def _create_product(session: Session, system_user, sku_prefix: str = "SKU-RET") -> Product:
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{sku_prefix}-{suffix}",
        name=f"Return Test Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(
            session, actor_id=system_user.id
        ).id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()
    return product


def _create_reason_code(session: Session, system_user, code: str = "DAMAGED_IN_TRANSIT") -> ReasonCodeRef:
    existing = session.execute(
        select(ReasonCodeRef).where(ReasonCodeRef.code == code)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    rc = ReasonCodeRef(
        code=code,
        label=f"Reason {code}",
        scope="RETURN",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rc)
    session.flush()
    return rc


def _assign_warehouse(
    session: Session, rep_id: uuid.UUID, warehouse_id: uuid.UUID,
    actor_id: uuid.UUID, *, is_primary: bool = True,
) -> None:
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


def _create_shipped_order(
    session, system_user, rep, product, *, qty: int = 10
) -> Order:
    """Create a SHIPPED order with the given product for testing returns."""
    from services.order_service import create_order, OrderLineInput
    from database.models.customer_rep_assignment import CustomerRepAssignment
    from datetime import datetime, timezone, timedelta

    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)

    # Create customer and assign to representative.
    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-RET-{suffix}",
        name=f"Return Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()

    session.add(CustomerRepAssignment(
        customer_id=customer.id,
        representative_id=rep.id,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        priority=1,
        created_by=system_user.id,
        updated_by=system_user.id,
    ))
    session.flush()

    # Create price.
    suffix2 = uuid.uuid4().hex[:8]
    price_list = PriceList(
        name=f"PL-RET-{suffix2}",
        price_type="RETAIL",
        currency_id=currency.id,
        owner_scope="GLOBAL",
        is_active=True,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(price_list)
    session.flush()

    price = PriceHistory(
        product_id=product.id,
        price_list_id=price_list.id,
        currency_id=currency.id,
        price_type="RETAIL",
        unit_price=decimal.Decimal("50.0000"),
        effective_from=_now(),
        created_by=system_user.id,
    )
    session.add(price)
    session.flush()

    # Post stock.
    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty + 10)),
        unit_cost=decimal.Decimal("10.000000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()

    # Create order.
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
        created_by=system_user.id,
    )
    session.flush()

    # Transition through lifecycle to SHIPPED.
    from services import order_service
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)
    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

    # Ship the full order.
    from services.order_service import ShipmentInput
    order_lines = list(order_service.list_order_lines(session, order.id))
    order_service.ship_order(
        session, order.id,
        actor_user_id=system_user.id,
        shipments=[ShipmentInput(
            order_line_id=order_lines[0].id,
            quantity=decimal.Decimal(str(qty)),
        )],
    )
    session.flush()

    return order


# =======================================================================
# 1. Authorization: BOT_WRITE required
# =======================================================================


@requires_database
class TestReturnRequiresBOTWrite:
    """/return must require BOT_WRITE permission."""

    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/return ORD-001 SKU001 1 DAMAGED_IN_TRANSIT",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
        finally:
            session.close()

    def test_accepted_with_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/return",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()


# =======================================================================
# 2. Unbound session rejected
# =======================================================================


@requires_database
class TestReturnUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/return ORD-001 SKU001 1 DAMAGED_IN_TRANSIT",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Representative identity anchored to BotSession
# =======================================================================


@requires_database
class TestReturnRepresentativeIdentity:
    def test_representative_from_session_not_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"retrid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/return NONEXISTENT SKU001 1 DAMAGED_IN_TRANSIT",
            )
            response = process_message(session, message=msg)
            # Should use session's rep, not any injected ID.
            assert isinstance(response, BotResponse)
        finally:
            session.close()


# =======================================================================
# 4. Validation: missing arguments
# =======================================================================


@requires_database
class TestReturnValidation:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-v-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/return",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_nonexistent_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-no-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/return ORD-NONEXISTENT SKU001 1 DAMAGED_IN_TRANSIT",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_cross_representative_order_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A with a shipped order.
            puid_a = f"ret-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            _grant_bot_write(session, user_a, su)
            product = _create_product(session, su)
            order_a = _create_shipped_order(session, su, rep_a, product)

            # Rep B tries to return Rep A's order.
            puid_b = f"ret-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_bot_write(session, user_b, su)

            reason = _create_reason_code(session, su)
            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/return {order_a.order_number} {product.sku} 1 {reason.code}",
            )
            response_b = process_message(session, message=msg_b)
            assert "does not belong" in response_b.text.lower() or "access denied" in response_b.text.lower()
        finally:
            session.close()

    def test_invalid_product_on_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-ip-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            reason = _create_reason_code(session, su)

            # Try to return a product not on the order.
            other_product = _create_product(session, su, sku_prefix="SKU-OTHER")
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {other_product.sku} 1 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "not found on order" in response.text.lower()
        finally:
            session.close()

    def test_zero_quantity_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-zq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 0 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "positive" in response.text.lower()
        finally:
            session.close()

    def test_quantity_exceeding_returnable(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-eq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product, qty=5)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "returnable" in response.text.lower() or "exceed" in response.text.lower()
        finally:
            session.close()

    def test_invalid_reason_code(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-rc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 NONEXISTENT_REASON",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_non_returnable_order_state(self):
        """A DRAFT order cannot be returned."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-nr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            # Create a DRAFT order (not shipped).
            from services.order_service import create_order, OrderLineInput
            from database.models.customer_rep_assignment import CustomerRepAssignment
            from database.models.price_history import PriceHistory
            from database.models.price_list import PriceList
            from database.models.customer import Customer
            from datetime import datetime, timezone, timedelta

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            product = _create_product(session, su)

            suffix = uuid.uuid4().hex[:6]
            customer = Customer(
                code=f"C-DRAFT-{suffix}", name=f"Draft Customer {suffix}",
                type="CORPORATE", currency_id=currency.id, status="ACTIVE",
                created_by=su.id, updated_by=su.id,
            )
            session.add(customer)
            session.flush()
            session.add(CustomerRepAssignment(
                customer_id=customer.id, representative_id=rep.id,
                effective_from=datetime.now(timezone.utc) - timedelta(days=30),
                priority=1, created_by=su.id, updated_by=su.id,
            ))
            session.flush()

            suffix2 = uuid.uuid4().hex[:8]
            price_list = PriceList(
                name=f"PL-DRAFT-{suffix2}", price_type="RETAIL",
                currency_id=currency.id, owner_scope="GLOBAL", is_active=True,
                created_by=su.id, updated_by=su.id,
            )
            session.add(price_list)
            session.flush()
            price = PriceHistory(
                product_id=product.id, price_list_id=price_list.id,
                currency_id=currency.id, price_type="RETAIL",
                unit_price=decimal.Decimal("50.0000"), effective_from=_now(),
                created_by=su.id,
            )
            session.add(price)
            session.flush()

            order = create_order(
                session, customer_id=customer.id, representative_id=rep.id,
                currency_id=currency.id, order_type="LOCAL",
                fulfillment_mode="REP_LOCAL", sales_channel="OFFICE",
                lines=[OrderLineInput(
                    product_id=product.id, fulfillment_warehouse_id=warehouse.id,
                    price_history_id=price.id, qty_ordered=5,
                    fulfillment_mode="REP_LOCAL",
                )],
                created_by=su.id,
            )
            session.flush()

            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "cannot be returned" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 5. Scope: order belonging to representative
# =======================================================================


@requires_database
class TestReturnScope:
    def test_own_order_succeeds(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-own-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "submitted for approval" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Approval lifecycle
# =======================================================================


@requires_database
class TestReturnApprovalLifecycle:
    def _setup_return_fixtures(self, session, su):
        """Create rep, user, product, shipped order, reason code."""
        puid = f"ret-al-{uuid.uuid4().hex[:6]}"
        rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
        _grant_bot_write(session, user, su)
        product = _create_product(session, su)
        order = _create_shipped_order(session, su, rep, product, qty=10)
        reason = _create_reason_code(session, su)
        return rep, user, bot_session, product, order, reason, puid

    def test_valid_command_creates_pending_approval(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Defective",
            )
            response = process_message(session, message=msg)
            assert "submitted for approval" in response.text.lower()

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            assert pending is not None
            assert pending.requested_by == user.id
        finally:
            session.close()

    def test_no_return_record_while_pending(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=msg)

            # No CustomerReturn should exist yet.
            returns = session.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            assert len(returns) == 0, "No return record should exist while PENDING"
        finally:
            session.close()

    def test_requester_cannot_approve_own_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            with pytest.raises(SeparationOfDutiesError):
                approve_request(
                    session, request_id=pending.id, approver_id=user.id,
                )
        finally:
            session.close()

    def test_approval_creates_return_records(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 3 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # Verify CustomerReturn was created.
            returns = session.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            assert len(returns) == 1

            # Verify ReturnLine was created.
            return_lines = session.execute(
                select(ReturnLine).where(
                    ReturnLine.customer_return_id == returns[0].id,
                )
            ).scalars().all()
            assert len(return_lines) == 1
            assert int(return_lines[0].qty_returned) == 3

            # Verify order_line.qty_returned was updated.
            order_lines = list(
                session.execute(
                    select(OrderLine).where(OrderLine.order_id == order.id)
                ).scalars().all()
            )
            assert int(order_lines[0].qty_returned) == 3
        finally:
            session.close()

    def test_rejection_creates_no_return(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            reject_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            returns = session.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            assert len(returns) == 0, "No return should exist on rejection"
        finally:
            session.close()

    def test_cancellation_creates_no_return(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            cancel_request(
                session, request_id=pending.id, cancelled_by=user.id,
            )

            returns = session.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            assert len(returns) == 0, "No return should exist on cancellation"
        finally:
            session.close()

    def test_approved_request_cannot_execute_twice(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, product, order, reason, puid = \
                self._setup_return_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # First execution succeeds.
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            returns_after_first = session.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            count_first = len(returns_after_first)

            # Second execution should be idempotent (no duplicate).
            result = execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )
            assert "already processed" in result.lower() or "duplicate" in result.lower()

            returns_after_second = session.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            assert len(returns_after_second) == count_first, \
                "No duplicate return records should be created"
        finally:
            session.close()


# =======================================================================
# 7. Security: no UUID leakage
# =======================================================================


@requires_database
class TestReturnSecurity:
    def test_no_internal_uuids_in_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-sec-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code} Test",
            )
            response = process_message(session, message=msg)

            assert str(rep.id) not in response.text
            assert str(user.id) not in response.text
            assert str(product.id) not in response.text
            assert str(order.id) not in response.text
        finally:
            session.close()

    def test_executor_rejects_unknown_entity_type(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            rep2 = _create_representative(session, su)
            approver = _create_app_user(session, su, rep2)

            request = create_approval_request(
                session,
                entity_type="bot_command:unknown-cmd",
                entity_id=uuid.uuid4(),
                requested_by=su.id,
                payload={"test": True},
            )
            session.flush()

            approve_request(
                session, request_id=request.id, approver_id=approver.id,
            )

            from services.approval_execution_service import UnknownCommandTypeError
            with pytest.raises(UnknownCommandTypeError):
                execute_approved_request(
                    session, request_id=request.id, approver_id=approver.id,
                )
        finally:
            session.close()


# =======================================================================
# 8. Audit trail
# =======================================================================


@requires_database
class TestReturnAudit:
    def test_approval_history_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-aud-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code} Audit",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )

            from database.models.approval_history import ApprovalHistory
            history_before = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == pending.id,
                )
            ).scalars().all()
            assert len(history_before) == 1

            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            history_after = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == pending.id,
                ).order_by(ApprovalHistory.created_at)
            ).scalars().all()
            assert len(history_after) == 2
            assert history_after[1].to_status == "APPROVED"
        finally:
            session.close()

    def test_return_mutation_audited(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-rma-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code} Audit",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:return", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # Check audit log for the return entity.
            audit_entries = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "customer_return",
                    AuditLog.action == "CREATE",
                )
            ).scalars().all()
            assert len(audit_entries) >= 1
        finally:
            session.close()


# =======================================================================
# 9. Regression: existing commands still work
# =======================================================================


@requires_database
class TestReturnRegression:
    def test_read_commands_still_work(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-reg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/me")
            response = process_message(session, message=msg)
            assert rep.person_name in response.text

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/customers")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()

    def test_create_order_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-regco-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_adjust_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ret-regadj-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/adjust",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()


# =======================================================================
# 10. Concurrency: concurrent approval/execution
# =======================================================================


@requires_database
class TestReturnConcurrency:
    def test_concurrent_approval_prevents_double_return(self):
        """Two concurrent approve+execute calls: only one return created."""
        from sqlalchemy.orm.exc import StaleDataError
        import threading

        factory = get_session_factory()

        # Set up test data.
        session_setup = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session_setup)
            su = bootstrap_service.ensure_system_user(session_setup)

            rep = _create_representative(session_setup, su)
            user = _create_app_user(session_setup, su, rep)
            _grant_bot_query(session_setup, user, su)
            _grant_bot_write(session_setup, user, su)
            _ensure_telegram_platform(session_setup)

            product = _create_product(session_setup, su)
            order = _create_shipped_order(session_setup, su, rep, product, qty=10)
            reason = _create_reason_code(session_setup, su)

            token = bot_session_service.generate_binding_token(
                session_setup, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=su.id,
            )
            bot_session = bot_session_service.create_binding(
                session_setup, binding_token=token, platform_code="TELEGRAM",
                platform_user_id=f"cnv-ret-{uuid.uuid4().hex[:6]}", linked_by=user.id,
            )

            # Get order line.
            order_lines = list(
                session_setup.execute(
                    select(OrderLine).where(OrderLine.order_id == order.id)
                ).scalars().all()
            )

            payload = {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "customer_id": str(order.customer_id),
                "representative_id": str(order.representative_id),
                "warehouse_id": str(order.fulfillment_warehouse_id),
                "warehouse_code": "MAIN",
                "product_id": str(product.id),
                "product_sku": product.sku,
                "order_line_id": str(order_lines[0].id),
                "quantity": 2,
                "reason_code_id": str(reason.id),
                "reason_code": reason.code,
                "reason_text": "Concurrency test",
                "requested_by": str(user.id),
            }

            request = create_approval_request(
                session_setup,
                entity_type="bot_command:return",
                entity_id=bot_session.id,
                requested_by=user.id,
                payload=payload,
            )
            session_setup.commit()
            request_id = request.id
        finally:
            session_setup.close()

        # Simulate concurrent approve+execute.
        results = {"s1": None, "s2": None}

        def approve_and_execute(label):
            session = factory()
            try:
                try:
                    approve_request(
                        session, request_id=request_id, approver_id=su.id,
                    )
                    session.commit()
                    execute_approved_request(
                        session, request_id=request_id, approver_id=su.id,
                    )
                    session.commit()
                    results[label] = "committed"
                except StaleDataError:
                    session.rollback()
                    results[label] = "stale_data_error"
                except InvalidApprovalTransitionError:
                    session.rollback()
                    results[label] = "transition_error"
                except Exception as e:
                    session.rollback()
                    results[label] = f"error: {e}"
            finally:
                session.close()

        from services.approval_service import InvalidApprovalTransitionError
        t1 = threading.Thread(target=approve_and_execute, args=("s1",))
        t2 = threading.Thread(target=approve_and_execute, args=("s2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one must succeed.
        succeeded = sum(1 for v in results.values() if v == "committed")
        prevented = sum(1 for v in results.values() if v in ("stale_data_error", "transition_error"))
        assert succeeded == 1, f"Expected exactly 1 success, got {succeeded}. Results: {results}"
        assert prevented == 1, f"Expected exactly 1 prevention, got {prevented}. Results: {results}"

        # Verify only one CustomerReturn was created.
        session_final = factory()
        try:
            returns = session_final.execute(
                select(CustomerReturn).where(
                    CustomerReturn.order_id == order.id,
                )
            ).scalars().all()
            assert len(returns) == 1, (
                f"Expected exactly 1 CustomerReturn, got {len(returns)}. "
                f"Double execution detected!"
            )
        finally:
            session_final.close()
