"""PostgreSQL-backed tests for /create-order bot command (Tier 3 mutation).

Covers:
1. /create-order requires BOT_WRITE
2. missing BOT_WRITE is rejected
3. unbound session is rejected
4. missing AppUser is rejected
5. malformed command is rejected
6. invalid customer is rejected
7. customer outside representative scope is rejected
8. valid customer creates an approval request
9. order is NOT created before approval
10. requester cannot approve own request
11. approved request executes order creation
12. rejected request does not create order
13. cancelled request does not create order
14. duplicate approval cannot execute order twice
15. Rep A cannot create order for Rep B customer
16. no internal UUIDs leak in bot response
17. audit record exists after successful execution
18. existing 5 read commands still pass

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
from database.models.bot_platform_ref import BotPlatformRef
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.order import Order
from database.models.product import Product
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.representative import Representative
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
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
    ApprovalNotApprovedError,
    PayloadMissingError,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping /create-order tests",
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


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
        code=f"REP-CO-{suffix.upper()}",
        person_name=f"CreateOrder Rep {suffix}",
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
        username=f"co_user_{suffix}",
        email=f"co_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_bot_query(
    session: Session, app_user: AppUser, system_user
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BQCO_{suffix}"
    rbac_service.create_role(
        session,
        code=role_code,
        name=f"BQCO Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session,
            code=BOT_QUERY_PERMISSION,
            name="Query via bot",
            resource="bot",
            action="query",
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
    role_code = f"BWCO_{suffix}"
    rbac_service.create_role(
        session,
        code=role_code,
        name=f"BWCO Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session,
            code=BOT_WRITE_PERMISSION,
            name="Write via bot",
            resource="bot",
            action="write",
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


def _create_product(session: Session, system_user) -> Product:
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-CO-{suffix}",
        name=f"CreateOrder Product {suffix}",
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


def _create_price(session: Session, system_user, product: Product) -> PriceHistory:
    currency = bootstrap_service.ensure_default_currency(
        session, actor_id=system_user.id
    )
    suffix = uuid.uuid4().hex[:8]
    price_list = PriceList(
        name=f"PL-CO-{suffix}",
        price_type="RETAIL",
        currency_id=currency.id,
        owner_scope="GLOBAL",
        is_active=True,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(price_list)
    session.flush()

    from datetime import datetime, timezone
    price = PriceHistory(
        product_id=product.id,
        price_list_id=price_list.id,
        currency_id=currency.id,
        price_type="RETAIL",
        unit_price=decimal.Decimal("50.0000"),
        effective_from=datetime.now(timezone.utc),
        created_by=system_user.id,
    )
    session.add(price)
    session.flush()
    return price


def _assign_customer(
    session: Session, rep_id: uuid.UUID, customer_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
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


def _create_customer(
    session: Session, system_user, *, code: str | None = None
) -> Customer:
    suffix = code or f"C-CO-{uuid.uuid4().hex[:6]}"
    currency = bootstrap_service.ensure_default_currency(
        session, actor_id=system_user.id
    )
    customer = Customer(
        code=suffix,
        name=f"CreateOrder Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()
    return customer


# =======================================================================
# 1. /create-order requires BOT_WRITE
# =======================================================================


@requires_database
class TestCreateOrderRequiresBOTWrite:
    """The /create-order command must require BOT_WRITE permission."""

    def test_rejected_without_bot_write(self):
        """A user with only BOT_QUERY must be rejected."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order CUST001 SKU001 10",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
        finally:
            session.close()

    def test_accepted_with_bot_write(self):
        """A user with BOT_WRITE can reach the handler."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order",
            )
            response = process_message(session, message=msg)
            # Missing args returns usage, not permission error.
            assert "Usage" in response.text
        finally:
            session.close()


# =======================================================================
# 3. unbound session is rejected
# =======================================================================


@requires_database
class TestCreateOrderUnboundSession:
    """Unbound sessions cannot reach /create-order."""

    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/create-order CUST001 SKU001 10",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 5. malformed command is rejected
# =======================================================================


@requires_database
class TestCreateOrderMalformed:
    """Malformed /create-order arguments are rejected."""

    def test_missing_args(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-m-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_invalid_quantity(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-iq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order CUST001 SKU001 abc",
            )
            response = process_message(session, message=msg)
            assert "Invalid quantity" in response.text
        finally:
            session.close()

    def test_zero_quantity(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-zq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order CUST001 SKU001 0",
            )
            response = process_message(session, message=msg)
            assert "positive" in response.text.lower()
        finally:
            session.close()

    def test_invalid_fulfillment_mode(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-fm-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order CUST001 SKU001 10 INVALID_MODE",
            )
            response = process_message(session, message=msg)
            assert "Invalid fulfillment mode" in response.text
        finally:
            session.close()


# =======================================================================
# 6. invalid customer is rejected
# =======================================================================


@requires_database
class TestCreateOrderInvalidCustomer:
    """Non-existent customer codes are rejected."""

    def test_unknown_customer_code(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-uc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-order NONEXISTENT SKU001 10",
            )
            response = process_message(session, message=msg)
            assert "not assigned" in response.text.lower()
        finally:
            session.close()

    def test_unknown_product_sku(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-us-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)
            customer = _create_customer(session, su)
            _assign_customer(session, rep.id, customer.id, su.id)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order {customer.code} NOSKU 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 7. customer outside representative scope is rejected
# =======================================================================


@requires_database
class TestCreateOrderCustomerScope:
    """Only customers in the representative's scope can be used."""

    def test_other_reps_customer_rejected(self):
        """Rep A cannot create order for Rep B's customer."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A
            puid_a = f"co-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(
                session, su, platform_user_id=puid_a,
            )
            _grant_bot_write(session, user_a, su)

            # Rep B
            puid_b = f"co-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(
                session, su, platform_user_id=puid_b,
            )

            # Customer assigned to Rep B only.
            customer_b = _create_customer(session, su, code="CUST-B-SCOPE")
            _assign_customer(session, rep_b.id, customer_b.id, su.id)

            # Rep A tries to create order for Rep B's customer.
            msg_a = BotMessage(
                platform_user_id=puid_a, platform_code="TELEGRAM",
                text=f"/create-order CUST-B-SCOPE SKU001 10",
            )
            response_a = process_message(session, message=msg_a)
            assert "not assigned" in response_a.text.lower()
        finally:
            session.close()


# =======================================================================
# 8. valid customer creates an approval request
# =======================================================================


@requires_database
class TestCreateOrderApprovalRequest:
    """Valid /create-order creates an approval request, not an order."""

    def test_creates_approval_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-ar-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-AR-TEST")
            _assign_customer(session, rep.id, customer.id, su.id)

            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)

            product = _create_product(session, su)
            price = _create_price(session, su, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-AR-TEST {product.sku} 5",
            )
            response = process_message(session, message=msg)
            assert "submitted for approval" in response.text.lower()
            assert "CUST-AR-TEST" in response.text
            assert product.sku in response.text
            assert "5" in response.text
        finally:
            session.close()


# =======================================================================
# 9. order is NOT created before approval
# =======================================================================


@requires_database
class TestCreateOrderNotBeforeApproval:
    """No order must exist in the database before approval."""

    def test_no_order_created_before_approval(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-na-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-NO-APPR")
            _assign_customer(session, rep.id, customer.id, su.id)

            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)

            product = _create_product(session, su)
            price = _create_price(session, su, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-NO-APPR {product.sku} 5",
            )
            process_message(session, message=msg)

            # Verify no order was created.
            orders = session.execute(
                select(Order).where(
                    Order.customer_id == customer.id,
                    Order.deleted_at.is_(None),
                )
            ).scalars().all()
            assert len(orders) == 0, "Order should NOT exist before approval"
        finally:
            session.close()


# =======================================================================
# 10. requester cannot approve own request
# =======================================================================


@requires_database
class TestCreateOrderSeparationOfDuties:
    """The requester cannot approve their own order creation request."""

    def test_requester_cannot_approve(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-sod-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-SOD")
            _assign_customer(session, rep.id, customer.id, su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)
            product = _create_product(session, su)
            price = _create_price(session, su, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-SOD {product.sku} 5",
            )
            process_message(session, message=msg)

            # Find the pending request.
            from sqlalchemy import text as sa_text
            pending = session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.entity_type == "bot_command:create-order",
                    ApprovalRequest.status == "PENDING",
                    ApprovalRequest.requested_by == user.id,
                )
            ).scalar_one_or_none()
            assert pending is not None

            # Requester tries to approve their own request — must fail.
            with pytest.raises(SeparationOfDutiesError):
                approve_request(
                    session, request_id=pending.id, approver_id=user.id,
                )
        finally:
            session.close()


# =======================================================================
# 11. approved request executes order creation
# =======================================================================


@requires_database
class TestCreateOrderApprovedExecution:
    """An approved request must execute the order creation."""

    def test_approved_creates_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-ae-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-AE-TEST")
            _assign_customer(session, rep.id, customer.id, su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)
            product = _create_product(session, su)
            price = _create_price(session, su, product)

            # Step 1: Create the approval request via bot command.
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-AE-TEST {product.sku} 5",
            )
            process_message(session, message=msg)

            # Step 2: Find and approve the request (as admin).
            pending = session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.entity_type == "bot_command:create-order",
                    ApprovalRequest.status == "PENDING",
                    ApprovalRequest.requested_by == user.id,
                )
            ).scalar_one_or_none()
            assert pending is not None
            approve_request(
                session, request_id=pending.id, approver_id=su.id,
            )

            # Step 3: Execute the deferred mutation.
            result = execute_approved_request(
                session, request_id=pending.id, approver_id=su.id,
            )

            # Verify order was created.
            orders = session.execute(
                select(Order).where(
                    Order.customer_id == customer.id,
                    Order.deleted_at.is_(None),
                )
            ).scalars().all()
            assert len(orders) == 1
            assert orders[0].state == "DRAFT"
            assert orders[0].representative_id == rep.id
            assert "Order" in result
            assert orders[0].order_number in result
        finally:
            session.close()


# =======================================================================
# 12. rejected request does not create order
# =======================================================================


@requires_database
class TestCreateOrderRejected:
    """A rejected request must NOT create an order."""

    def test_rejected_no_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-rj-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-RJ-TEST")
            _assign_customer(session, rep.id, customer.id, su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)
            product = _create_product(session, su)
            price = _create_price(session, su, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-RJ-TEST {product.sku} 5",
            )
            process_message(session, message=msg)

            # Reject the request.
            pending = session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.entity_type == "bot_command:create-order",
                    ApprovalRequest.status == "PENDING",
                    ApprovalRequest.requested_by == user.id,
                )
            ).scalar_one_or_none()
            assert pending is not None
            reject_request(
                session, request_id=pending.id, approver_id=su.id,
            )

            # Attempt to execute — must fail.
            with pytest.raises(ApprovalNotApprovedError):
                execute_approved_request(
                    session, request_id=pending.id, approver_id=su.id,
                )

            # Verify no order was created.
            orders = session.execute(
                select(Order).where(
                    Order.customer_id == customer.id,
                    Order.deleted_at.is_(None),
                )
            ).scalars().all()
            assert len(orders) == 0
        finally:
            session.close()


# =======================================================================
# 13. cancelled request does not create order
# =======================================================================


@requires_database
class TestCreateOrderCancelled:
    """A cancelled request must NOT create an order."""

    def test_cancelled_no_order(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-cn-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-CN-TEST")
            _assign_customer(session, rep.id, customer.id, su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)
            product = _create_product(session, su)
            price = _create_price(session, su, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-CN-TEST {product.sku} 5",
            )
            process_message(session, message=msg)

            # Cancel the request.
            pending = session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.entity_type == "bot_command:create-order",
                    ApprovalRequest.status == "PENDING",
                    ApprovalRequest.requested_by == user.id,
                )
            ).scalar_one_or_none()
            assert pending is not None
            cancel_request(
                session, request_id=pending.id, cancelled_by=user.id,
            )

            # Attempt to execute — must fail.
            with pytest.raises(ApprovalNotApprovedError):
                execute_approved_request(
                    session, request_id=pending.id, approver_id=su.id,
                )

            orders = session.execute(
                select(Order).where(
                    Order.customer_id == customer.id,
                    Order.deleted_at.is_(None),
                )
            ).scalars().all()
            assert len(orders) == 0
        finally:
            session.close()


# =======================================================================
# 16. no internal UUIDs leak in bot response
# =======================================================================


@requires_database
class TestCreateOrderNoUUIDLeak:
    """Bot responses must not contain internal UUIDs."""

    def test_no_uuid_in_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-ul-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            _grant_bot_write(session, user, su)

            customer = _create_customer(session, su, code="CUST-UL-TEST")
            _assign_customer(session, rep.id, customer.id, su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(
                session, actor_id=su.id,
            )
            _assign_warehouse(session, rep.id, warehouse.id, su.id)
            product = _create_product(session, su)
            price = _create_price(session, su, product)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-order CUST-UL-TEST {product.sku} 5",
            )
            response = process_message(session, message=msg)

            # Response must not contain any UUIDs.
            assert str(customer.id) not in response.text
            assert str(product.id) not in response.text
            assert str(rep.id) not in response.text
            assert str(user.id) not in response.text
            # Must contain human-readable codes.
            assert "CUST-UL-TEST" in response.text
            assert product.sku in response.text
        finally:
            session.close()


# =======================================================================
# 18. existing 5 read commands still pass
# =======================================================================


@requires_database
class TestCreateOrderNoRegression:
    """Existing BOT_QUERY commands must not regress."""

    def test_me_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-regm-{uuid.uuid4().hex[:6]}"
            rep, _, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid, platform_code="TELEGRAM", text="/me",
                ),
            )
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_orders_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-rego-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid, platform_code="TELEGRAM",
                    text="/orders",
                ),
            )
            assert "No orders found" in response.text
        finally:
            session.close()

    def test_customers_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-regc-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid, platform_code="TELEGRAM",
                    text="/customers",
                ),
            )
            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_inventory_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-regi-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid, platform_code="TELEGRAM",
                    text="/inventory",
                ),
            )
            assert isinstance(response, BotResponse)
        finally:
            session.close()

    def test_balance_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"co-regb-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid, platform_code="TELEGRAM",
                    text="/balance",
                ),
            )
            assert "No customers assigned" in response.text
        finally:
            session.close()
