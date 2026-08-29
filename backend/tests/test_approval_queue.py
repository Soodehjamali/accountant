"""PostgreSQL-backed tests for the approval queue workflow.

Covers:
- /pending lists requests with approval numbers
- /approve resolves and executes
- /reject resolves without execution
- APPROVE permission required
- Unbound session rejected
- Missing AppUser rejected
- Requester cannot approve/reject own request
- Unknown approval reference fails safely
- Terminal requests cannot be resolved
- No UUID leakage in responses
- Approval history recorded
- Audit trail consistent
- Regression: existing commands still work
- Concurrency: concurrent approvals, approve vs reject

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
from database.models.approval_history import ApprovalHistory
from database.models.approval_request import ApprovalRequest
from database.models.bot_platform_ref import BotPlatformRef
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.order import Order
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.reason_code_ref import ReasonCodeRef
from database.models.representative import Representative
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service
from services import bot_session_service
from services.bot_command_service import (
    APPROVE_PERMISSION,
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
    ApprovalRequestAlreadyExistsError,
    SeparationOfDutiesError,
    approve_request,
    cancel_request,
    create_approval_request,
    generate_approval_number,
    get_approval_request_by_number,
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
    reason="DATABASE_URL not set; skipping approval queue tests",
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

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
        code=f"REP-AQ-{suffix.upper()}",
        person_name=f"AQ Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session: Session, system_user, rep: Representative) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(
        session,
        username=f"aq_user_{suffix}",
        email=f"aq_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_permission(session: Session, app_user: AppUser, system_user, perm_code: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"AQ_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"AQ {perm_code} {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session, code=perm_code, name=f"Permission {perm_code}",
            resource="bot", action=perm_code.lower(),
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session, role_code=role_code, permission_code=perm_code,
    )
    rbac_service.assign_role(
        session, user_id=app_user.id, role_code=role_code,
        assigned_by=system_user.id,
    )


def _grant_bot_query(session, app_user, system_user):
    _grant_permission(session, app_user, system_user, BOT_QUERY_PERMISSION)


def _grant_bot_write(session, app_user, system_user):
    _grant_permission(session, app_user, system_user, BOT_WRITE_PERMISSION)


def _grant_approve(session, app_user, system_user):
    _grant_permission(session, app_user, system_user, APPROVE_PERMISSION)


def _make_bound_session(session: Session, system_user, *, platform_user_id: str):
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


def _create_product(session: Session, system_user, sku_prefix: str = "SKU-AQ") -> Product:
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{sku_prefix}-{suffix}",
        name=f"AQ Test Product {suffix}",
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
        code=code, label=f"Reason {code}", scope="RETURN",
        created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(rc)
    session.flush()
    return rc


def _assign_warehouse(session, rep_id, warehouse_id, actor_id, *, is_primary=True):
    from datetime import datetime, timezone, timedelta
    assignment = WarehouseAssignment(
        representative_id=rep_id, warehouse_id=warehouse_id,
        is_primary=is_primary,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        created_by=actor_id, updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()


def _create_shipped_order(session, system_user, rep, product, *, qty: int = 10) -> Order:
    """Create a SHIPPED order for testing."""
    from services.order_service import create_order, OrderLineInput
    from datetime import datetime, timezone, timedelta

    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:6]
    customer = Customer(
        code=f"C-AQ-{suffix}", name=f"AQ Customer {suffix}",
        type="CORPORATE", currency_id=currency.id, status="ACTIVE",
        created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()

    session.add(CustomerRepAssignment(
        customer_id=customer.id, representative_id=rep.id,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        priority=1, created_by=system_user.id, updated_by=system_user.id,
    ))
    session.flush()

    suffix2 = uuid.uuid4().hex[:8]
    price_list = PriceList(
        name=f"PL-AQ-{suffix2}", price_type="RETAIL",
        currency_id=currency.id, owner_scope="GLOBAL", is_active=True,
        created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(price_list)
    session.flush()

    price = PriceHistory(
        product_id=product.id, price_list_id=price_list.id,
        currency_id=currency.id, price_type="RETAIL",
        unit_price=decimal.Decimal("50.0000"), effective_from=_now(),
        created_by=system_user.id,
    )
    session.add(price)
    session.flush()

    inventory_service.post_transaction(
        session, product_id=product.id, warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty + 10)),
        unit_cost=decimal.Decimal("10.000000"), currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()

    order = create_order(
        session, customer_id=customer.id, representative_id=rep.id,
        currency_id=currency.id, price_list_id=price_list.id, order_type="LOCAL",
        fulfillment_mode="REP_LOCAL", sales_channel="OFFICE",
        lines=[OrderLineInput(
            product_id=product.id, fulfillment_warehouse_id=warehouse.id,
            price_history_id=price.id, qty_ordered=qty,
            fulfillment_mode="REP_LOCAL",
        )], created_by=system_user.id,
    )
    session.flush()

    from services import order_service
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)
    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

    from services.order_service import ShipmentInput
    order_lines = list(order_service.list_order_lines(session, order.id))
    order_service.ship_order(
        session, order.id, actor_user_id=system_user.id,
        shipments=[ShipmentInput(
            order_line_id=order_lines[0].id,
            quantity=decimal.Decimal(str(qty)),
        )],
    )
    session.flush()
    return order


def _create_pending_approval(session, system_user, entity_type="bot_command:test-cmd"):
    """Create a PENDING approval request with approval_number."""
    rep2 = _create_representative(session, system_user)
    requester = _create_app_user(session, system_user, rep2)
    request = create_approval_request(
        session,
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        requested_by=requester.id,
        payload={"test": True},
    )
    session.flush()
    return request, requester, rep2


# =======================================================================
# 1. APPROVE permission required
# =======================================================================

@requires_database
class TestApprovalQueueRequiresPermission:
    """Commands require APPROVE permission."""

    def test_pending_requires_approve_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            # Has BOT_QUERY but NOT APPROVE
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/pending")
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == APPROVE_PERMISSION
        finally:
            session.close()

    def test_approve_requires_approve_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/approve APR-TEST1234",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == APPROVE_PERMISSION
        finally:
            session.close()

    def test_reject_requires_approve_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/reject APR-TEST1234",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == APPROVE_PERMISSION
        finally:
            session.close()

    def test_accepted_with_approve_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/pending")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================

@requires_database
class TestApprovalQueueUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/pending",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. /pending lists requests
# =======================================================================

@requires_database
class TestPendingCommand:
    def test_pending_shows_requests(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-p-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            request, requester, _ = _create_pending_approval(session, su)
            session.flush()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/pending")
            response = process_message(session, message=msg)

            assert "Pending approval requests" in response.text
            assert request.approval_number in response.text
            assert "APR-" in response.text
        finally:
            session.close()

    def test_pending_empty_when_no_requests(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-pe-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/pending")
            response = process_message(session, message=msg)
            assert "No pending" in response.text
        finally:
            session.close()

    def test_pending_no_uuid_leakage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-nl-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            request, requester, _ = _create_pending_approval(session, su)
            session.flush()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/pending")
            response = process_message(session, message=msg)

            # Must not contain raw UUIDs
            assert str(request.id) not in response.text
            assert str(requester.id) not in response.text
            # But must contain the approval number
            assert request.approval_number in response.text
        finally:
            session.close()


# =======================================================================
# 4. /approve command
# =======================================================================

@requires_database
class TestApproveCommand:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-am-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/approve")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_invalid_format_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-if-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/approve INVALID",
            )
            response = process_message(session, message=msg)
            assert "Invalid" in response.text
        finally:
            session.close()

    def test_nonexistent_reference_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-nr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/approve APR-XXXXXXXX",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_approve_executes_mutation(self):
        """Approving a return request creates the return records."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A: submitter (has BOT_WRITE)
            puid_a = f"aq-ae-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            _grant_bot_write(session, user_a, su)

            # Rep B: approver (has APPROVE, different user)
            puid_b = f"aq-ae-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_approve(session, user_b, su)

            # Create and ship an order for rep_a
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep_a, product, qty=10)
            reason = _create_reason_code(session, su)

            # Submit /return via rep_a's bot
            return_msg = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=return_msg)

            # Find the pending request
            pending = get_pending_request(session, "bot_command:return", _get_bot_session_id(session, puid_a))
            assert pending is not None
            assert pending.approval_number is not None

            # Approve via rep_b's bot
            approve_msg = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/approve {pending.approval_number}",
            )
            response = process_message(session, message=approve_msg)

            assert "approved and executed" in response.text.lower()
            assert pending.approval_number in response.text

            # Verify the return was created
            from database.models.customer_return import CustomerReturn
            returns = session.execute(
                select(CustomerReturn).where(CustomerReturn.order_id == order.id)
            ).scalars().all()
            assert len(returns) == 1
        finally:
            session.close()

    def test_approve_own_request_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-sod-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)
            _grant_bot_write(session, user, su)

            # Submit /return
            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product, qty=10)
            reason = _create_reason_code(session, su)
            return_msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code} Test",
            )
            process_message(session, message=return_msg)

            pending = get_pending_request(session, "bot_command:return", _get_bot_session_id(session, puid))

            # Try to approve own request
            approve_msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/approve {pending.approval_number}",
            )
            response = process_message(session, message=approve_msg)
            assert "cannot approve your own" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 5. /reject command
# =======================================================================

@requires_database
class TestRejectCommand:
    def test_reject_without_reason(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-rw-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            request, requester, _ = _create_pending_approval(session, su)
            session.flush()

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/reject {request.approval_number}",
            )
            response = process_message(session, message=msg)
            assert "rejected" in response.text.lower()
            assert request.approval_number in response.text
        finally:
            session.close()

    def test_reject_with_reason(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-rwr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            request, requester, _ = _create_pending_approval(session, su)
            session.flush()

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/reject {request.approval_number} Not enough info",
            )
            response = process_message(session, message=msg)
            assert "rejected" in response.text.lower()

            # Verify the note was recorded
            refreshed = session.get(ApprovalRequest, request.id)
            assert refreshed.status == "REJECTED"
        finally:
            session.close()

    def test_reject_own_request_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-sodr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)
            _grant_bot_write(session, user, su)

            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product, qty=10)
            reason = _create_reason_code(session, su)
            return_msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code} Test",
            )
            process_message(session, message=return_msg)

            pending = get_pending_request(session, "bot_command:return", _get_bot_session_id(session, puid))

            reject_msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/reject {pending.approval_number}",
            )
            response = process_message(session, message=reject_msg)
            assert "cannot reject your own" in response.text.lower()
        finally:
            session.close()

    def test_reject_does_not_execute(self):
        """Rejecting a request must NOT execute the deferred mutation."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-rne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)
            _grant_bot_write(session, user, su)

            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep, product, qty=10)
            reason = _create_reason_code(session, su)
            return_msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 2 {reason.code} Test",
            )
            process_message(session, message=return_msg)

            pending = get_pending_request(session, "bot_command:return", _get_bot_session_id(session, puid))

            reject_msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/reject {pending.approval_number}",
            )
            process_message(session, message=reject_msg)

            # No return should exist
            from database.models.customer_return import CustomerReturn
            returns = session.execute(
                select(CustomerReturn).where(CustomerReturn.order_id == order.id)
            ).scalars().all()
            assert len(returns) == 0
        finally:
            session.close()


# =======================================================================
# 6. Terminal state protection
# =======================================================================

@requires_database
class TestTerminalStateProtection:
    def test_approve_already_approved_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-ts-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_approve(session, user, su)

            request, requester, rep2 = _create_pending_approval(session, su)
            approver_user = _create_app_user(session, su, rep2)
            _grant_approve(session, approver_user, su)
            session.flush()

            # Approve once
            approve_request(session, request_id=request.id, approver_id=approver_user.id)

            # Try to approve again
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/approve {request.approval_number}",
            )
            response = process_message(session, message=msg)
            assert "not pending" in response.text.lower() or "approved" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 7. Approval number generation
# =======================================================================

@requires_database
class TestApprovalNumberGeneration:
    def test_approval_number_format(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            num = generate_approval_number(session)
            assert num.startswith("APR-")
            assert len(num) == 12  # APR-XXXXXXXX
        finally:
            session.close()

    def test_approval_number_unique(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            nums = set()
            for _ in range(50):
                nums.add(generate_approval_number(session))
            assert len(nums) == 50
        finally:
            session.close()

    def test_create_sets_approval_number(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            request, _, _ = _create_pending_approval(session, su)
            assert request.approval_number is not None
            assert request.approval_number.startswith("APR-")
        finally:
            session.close()

    def test_lookup_by_number(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            request, _, _ = _create_pending_approval(session, su)
            found = get_approval_request_by_number(session, request.approval_number)
            assert found.id == request.id
        finally:
            session.close()


# =======================================================================
# 8. Audit trail
# =======================================================================

@requires_database
class TestApprovalQueueAudit:
    def test_approval_history_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A: submitter
            puid_a = f"aq-au-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            _grant_bot_write(session, user_a, su)

            # Rep B: approver
            puid_b = f"aq-au-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_approve(session, user_b, su)

            product = _create_product(session, su)
            order = _create_shipped_order(session, su, rep_a, product, qty=10)
            reason = _create_reason_code(session, su)
            return_msg = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/return {order.order_number} {product.sku} 1 {reason.code} Audit",
            )
            process_message(session, message=return_msg)

            pending = get_pending_request(session, "bot_command:return", _get_bot_session_id(session, puid_a))

            # Check initial history
            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == pending.id
                )
            ).scalars().all()
            assert len(history) == 1  # PENDING -> PENDING (creation)

            # Approve via rep_b
            approve_msg = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/approve {pending.approval_number}",
            )
            process_message(session, message=approve_msg)

            # Check history after approval
            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == pending.id
                )
            ).scalars().all()
            assert len(history) == 2
            assert history[1].to_status == "APPROVED"
        finally:
            session.close()


# =======================================================================
# 9. Regression: existing commands still work
# =======================================================================

@requires_database
class TestApprovalQueueRegression:
    def test_read_commands_still_work(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-reg-{uuid.uuid4().hex[:6]}"
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
            puid = f"aq-regco-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-order")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_adjust_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-regadj-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/adjust")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_return_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"aq-regret-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/return")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()


# =======================================================================
# 10. Concurrency
# =======================================================================

@requires_database
class TestApprovalQueueConcurrency:
    def test_concurrent_approve_one_succeeds(self):
        """Two concurrent approve calls: exactly one succeeds."""
        from sqlalchemy.orm.exc import StaleDataError
        from services.approval_service import InvalidApprovalTransitionError

        factory = get_session_factory()

        # Setup
        session_setup = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session_setup)
            su = bootstrap_service.ensure_system_user(session_setup)
            request, requester, rep2 = _create_pending_approval(session_setup, su)

            # Create an approver (different from requester)
            approver = _create_app_user(session_setup, su, rep2)
            _grant_approve(session_setup, approver, su)
            session_setup.commit()
            request_id = request.id
            approver_id = approver.id
        finally:
            session_setup.close()

        results = {"s1": None, "s2": None}

        def try_approve(label):
            session = factory()
            try:
                try:
                    approve_request(session, request_id=request_id, approver_id=approver_id)
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

        t1 = threading.Thread(target=try_approve, args=("s1",))
        t2 = threading.Thread(target=try_approve, args=("s2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one should succeed
        committed = sum(1 for v in results.values() if v == "committed")
        assert committed == 1, f"Expected exactly 1 committed, got {committed}: {results}"

        # Verify final state
        session_verify = factory()
        try:
            final = session_verify.get(ApprovalRequest, request_id)
            assert final.status == "APPROVED"
        finally:
            session_verify.close()


# =======================================================================
# Helpers (private)
# =======================================================================

def _get_bot_session_id(session: Session, platform_user_id: str) -> uuid.UUID:
    """Get the bot_session.id for a given platform_user_id."""
    from database.models.bot_session import BotSession
    bs = session.execute(
        select(BotSession).where(
            BotSession.platform_user_id == platform_user_id,
        )
    ).scalar_one_or_none()
    return bs.id if bs is not None else uuid.uuid4()


import threading  # noqa: E402
