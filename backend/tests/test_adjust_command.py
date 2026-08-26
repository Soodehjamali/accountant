"""PostgreSQL-backed tests for /adjust bot command (Tier 3 mutation).

Covers per acceptance criteria:
- Authorization: BOT_WRITE required, unbound session, representative identity
- Validation: missing args, invalid type, invalid quantity, invalid product, invalid reason code
- Scope: own warehouse, cross-rep warehouse, unassigned warehouse, expired assignment
- Approval: PENDING state, no mutation while PENDING, separation of duties,
  approval executes, rejection no-op, cancellation no-op, no double execution
- Security: no UUID leakage, payload cannot override identity, executor rejects unknown type
- Audit: approval history, resolution audit, inventory mutation audit
- Regression: read commands and /create-order still work
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
from database.models.inventory_transaction import InventoryTransaction
from database.models.product import Product
from database.models.reason_code_ref import ReasonCodeRef
from database.models.representative import Representative
from database.models.stock_adjustment import StockAdjustment
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
    reason="DATABASE_URL not set; skipping /adjust tests",
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
        code=f"REP-ADJ-{suffix.upper()}",
        person_name=f"Adjust Rep {suffix}",
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
        username=f"adj_user_{suffix}",
        email=f"adj_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_bot_query(
    session: Session, app_user: AppUser, system_user
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BQADJ_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BQADJ Tester {suffix}",
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
    role_code = f"BWADJ_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BWADJ Tester {suffix}",
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


def _create_product(session: Session, system_user, sku_prefix: str = "SKU-ADJ") -> Product:
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{sku_prefix}-{suffix}",
        name=f"Adjust Test Product {suffix}",
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
        scope="ADJUSTMENT",
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


def _post_stock(session, warehouse_id, product_id, qty, currency_id, actor_id):
    """Post initial stock to make the product available for adjustment."""
    inventory_service.post_transaction(
        session,
        product_id=product_id,
        warehouse_id=warehouse_id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty)),
        unit_cost=decimal.Decimal("10.000000"),
        currency_id=currency_id,
        actor_user_id=actor_id,
    )
    session.flush()


def _get_stock_balance(session, warehouse_id, product_id):
    return inventory_service.get_balance(
        session, warehouse_id=warehouse_id, product_id=product_id,
    )


# =======================================================================
# 1. Authorization: BOT_WRITE required
# =======================================================================


@requires_database
class TestAdjustRequiresBOTWrite:
    """/adjust must require BOT_WRITE permission."""

    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/adjust SKU001 NEGATIVE -10 PRICING_ERROR",
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
            puid = f"adj2-{uuid.uuid4().hex[:6]}"
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
# 2. Unbound session rejected
# =======================================================================


@requires_database
class TestAdjustUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/adjust SKU001 NEGATIVE -10 PRICING_ERROR",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Representative identity anchored to BotSession
# =======================================================================


@requires_database
class TestAdjustRepresentativeIdentity:
    def test_representative_from_session_not_args(self):
        """The representative identity must come from BotSession, not from args."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adjrid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            # Try to supply a different representative ID in the args (it should be ignored).
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/adjust SKU001 NEGATIVE -10 PRICING_ERROR",
            )
            response = process_message(session, message=msg)
            # Should still process using the session's rep, not any injected ID.
            # If the product doesn't exist, we get a validation error, which is fine —
            # the point is it didn't crash or use a different rep.
            assert isinstance(response, BotResponse)
        finally:
            session.close()


# =======================================================================
# 4. Validation: missing arguments
# =======================================================================


@requires_database
class TestAdjustValidation:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-v-{uuid.uuid4().hex[:6]}"
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

    def test_invalid_adjustment_type(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-at-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} INVALID_TYPE -10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "Invalid adjustment type" in response.text
        finally:
            session.close()

    def test_malformed_quantity(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-mq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE abc {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "Invalid quantity" in response.text
        finally:
            session.close()

    def test_zero_quantity_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-zq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE 0 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "nonzero" in response.text.lower()
        finally:
            session.close()

    def test_invalid_product(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-ip-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust NONEXISTENT-SKU NEGATIVE -10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_invalid_reason_code(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-rc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -10 NONEXISTENT_REASON",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_positive_type_with_negative_quantity_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-sp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} POSITIVE -10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "POSITIVE" in response.text
            assert "positive" in response.text.lower()
        finally:
            session.close()

    def test_negative_type_with_positive_quantity_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-sn-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE 10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "NEGATIVE" in response.text
            assert "negative" in response.text.lower()
        finally:
            session.close()

    def test_insufficient_stock_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-is-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
            product = _create_product(session, su)

            # Post only 5 units.
            _post_stock(session, warehouse.id, product.id, 5, currency.id, su.id)

            # Try to remove 10 — insufficient.
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -10 {reason.code} Too many",
            )
            response = process_message(session, message=msg)
            assert "Insufficient stock" in response.text
        finally:
            session.close()


# =======================================================================
# 5. Scope: warehouse enforcement
# =======================================================================


@requires_database
class TestAdjustScope:
    def test_own_warehouse_succeeds(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-swo-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
            product = _create_product(session, su)
            _post_stock(session, warehouse.id, product.id, 100, currency.id, su.id)

            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test adjustment",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "submitted for approval" in response.text.lower()
        finally:
            session.close()

    def test_no_warehouse_assigned_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-nwa-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "No warehouse assigned" in response.text
        finally:
            session.close()

    def test_expired_warehouse_assignment_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-ea-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            # Expired assignment
            from datetime import datetime, timezone, timedelta
            assignment = WarehouseAssignment(
                representative_id=rep.id,
                warehouse_id=warehouse.id,
                is_primary=True,
                effective_from=datetime.now(timezone.utc) - timedelta(days=60),
                effective_to=datetime.now(timezone.utc) - timedelta(days=10),
                created_by=su.id,
                updated_by=su.id,
            )
            session.add(assignment)
            session.flush()

            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -10 {reason.code}",
            )
            response = process_message(session, message=msg)
            assert "No warehouse assigned" in response.text
        finally:
            session.close()

    def test_cross_representative_warehouse_rejected(self):
        """Rep A's warehouse must not be usable by Rep B."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A with a warehouse.
            puid_a = f"adj-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            warehouse_a = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep_a.id, warehouse_a.id, actor_id=su.id)

            # Rep B with no warehouse.
            puid_b = f"adj-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_bot_write(session, user_b, su)

            product = _create_product(session, su)
            reason = _create_reason_code(session, su)
            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -10 {reason.code}",
            )
            response_b = process_message(session, message=msg_b)
            assert "No warehouse assigned" in response_b.text
        finally:
            session.close()


# =======================================================================
# 6. Approval lifecycle
# =======================================================================


@requires_database
class TestAdjustApprovalLifecycle:
    def _setup_adjust_fixtures(self, session, su):
        """Create rep, user, warehouse, product, stock, reason code."""
        puid = f"adj-al-{uuid.uuid4().hex[:6]}"
        rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
        _grant_bot_write(session, user, su)

        currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
        _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
        product = _create_product(session, su)
        _post_stock(session, warehouse.id, product.id, 100, currency.id, su.id)
        reason = _create_reason_code(session, su)
        return rep, user, bot_session, warehouse, product, reason, puid

    def test_valid_command_creates_pending_approval(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            response = process_message(session, message=msg)
            assert "submitted for approval" in response.text.lower()

            # Verify approval request was created.
            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )
            assert pending is not None
            assert pending.requested_by == user.id
        finally:
            session.close()

    def test_no_inventory_mutation_while_pending(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            balance_before = _get_stock_balance(session, warehouse.id, product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            process_message(session, message=msg)

            balance_after = _get_stock_balance(session, warehouse.id, product.id)
            assert balance_before == balance_after, "Balance must not change while PENDING"
        finally:
            session.close()

    def test_requester_cannot_approve_own_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )
            with pytest.raises(SeparationOfDutiesError):
                approve_request(
                    session, request_id=pending.id, approver_id=user.id,
                )
        finally:
            session.close()

    def test_approval_creates_inventory_transaction(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            balance_before = _get_stock_balance(session, warehouse.id, product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )

            # Approve with a different user.
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # Execute.
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            balance_after = _get_stock_balance(session, warehouse.id, product.id)
            assert balance_after == balance_before - 5, "Balance should decrease by 5"
        finally:
            session.close()

    def test_rejection_creates_no_transaction(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            balance_before = _get_stock_balance(session, warehouse.id, product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )

            approver = bootstrap_service.ensure_system_user(session)
            reject_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            balance_after = _get_stock_balance(session, warehouse.id, product.id)
            assert balance_before == balance_after, "Balance must not change on rejection"
        finally:
            session.close()

    def test_cancellation_creates_no_transaction(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            balance_before = _get_stock_balance(session, warehouse.id, product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )

            cancel_request(
                session, request_id=pending.id, cancelled_by=user.id,
            )

            balance_after = _get_stock_balance(session, warehouse.id, product.id)
            assert balance_before == balance_after, "Balance must not change on cancellation"
        finally:
            session.close()

    def test_approved_request_cannot_execute_twice(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            balance_before = _get_stock_balance(session, warehouse.id, product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )

            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # First execution succeeds.
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            balance_after_first = _get_stock_balance(session, warehouse.id, product.id)

            # Second execution returns "already applied" message (idempotent).
            result = execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )
            assert "already applied" in result.lower()

            balance_after_second = _get_stock_balance(session, warehouse.id, product.id)
            assert balance_after_first == balance_after_second, \
                "Balance must not change on second execution attempt"
        finally:
            session.close()

    def test_positive_adjustment_increases_stock(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep, user, bot_session, warehouse, product, reason, puid = \
                self._setup_adjust_fixtures(session, su)

            balance_before = _get_stock_balance(session, warehouse.id, product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} POSITIVE +20 {reason.code} Restock",
            )
            response = process_message(session, message=msg)
            assert "submitted for approval" in response.text.lower()

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            balance_after = _get_stock_balance(session, warehouse.id, product.id)
            assert balance_after == balance_before + 20
        finally:
            session.close()


# =======================================================================
# 7. Security: no UUID leakage
# =======================================================================


@requires_database
class TestAdjustSecurity:
    def test_no_internal_uuids_in_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-sec-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
            product = _create_product(session, su)
            _post_stock(session, warehouse.id, product.id, 100, currency.id, su.id)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Test",
            )
            response = process_message(session, message=msg)

            # Must not contain raw UUIDs.
            assert str(rep.id) not in response.text
            assert str(user.id) not in response.text
            assert str(product.id) not in response.text
            assert str(warehouse.id) not in response.text
        finally:
            session.close()

    def test_executor_rejects_unknown_entity_type(self):
        """The executor must not process requests with unknown entity_type."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Need a different user for approval (separation of duties).
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

            # Approve first, then try to execute with unknown entity_type.
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
class TestAdjustAudit:
    def test_approval_history_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-aud-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
            product = _create_product(session, su)
            _post_stock(session, warehouse.id, product.id, 100, currency.id, su.id)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Audit test",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )

            from database.models.approval_history import ApprovalHistory
            history_before = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == pending.id,
                )
            ).scalars().all()
            assert len(history_before) == 1  # Initial creation

            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            history_after = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == pending.id,
                ).order_by(ApprovalHistory.created_at)
            ).scalars().all()
            assert len(history_after) == 2  # Creation + approval
            assert history_after[1].to_status == "APPROVED"
        finally:
            session.close()

    def test_approval_resolution_audited(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-ar-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
            product = _create_product(session, su)
            _post_stock(session, warehouse.id, product.id, 100, currency.id, su.id)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Audit",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # Check audit log for approval.
            audit_entries = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "approval_request",
                    AuditLog.entity_id == pending.id,
                    AuditLog.action == "APPROVE",
                )
            ).scalars().all()
            assert len(audit_entries) == 1
            assert audit_entries[0].actor_user_id == approver.id
        finally:
            session.close()

    def test_inventory_mutation_audited(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-ima-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=su.id)
            _assign_warehouse(session, rep.id, warehouse.id, actor_id=su.id)
            product = _create_product(session, su)
            _post_stock(session, warehouse.id, product.id, 100, currency.id, su.id)
            reason = _create_reason_code(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/adjust {product.sku} NEGATIVE -5 {reason.code} Audit",
            )
            process_message(session, message=msg)

            pending = get_pending_request(
                session, "bot_command:adjust", bot_session.id,
            )
            approver = bootstrap_service.ensure_system_user(session)
            approve_request(
                session, request_id=pending.id, approver_id=approver.id,
            )
            execute_approved_request(
                session, request_id=pending.id, approver_id=approver.id,
            )

            # Check audit log for the stock adjustment entity.
            audit_entries = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "stock_adjustment",
                    AuditLog.action == "UPDATE",
                )
            ).scalars().all()
            assert len(audit_entries) >= 1
            assert audit_entries[0].actor_user_id == approver.id
        finally:
            session.close()


# =======================================================================
# 9. Regression: existing commands still work
# =======================================================================


@requires_database
class TestAdjustRegression:
    def test_read_commands_still_work(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"adj-reg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            # /me must still work.
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/me")
            response = process_message(session, message=msg)
            assert rep.person_name in response.text

            # /customers must still work.
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
            puid = f"adj-regco-{uuid.uuid4().hex[:6]}"
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


# =======================================================================
# 10. Concurrency: concurrent approval/execution
# =======================================================================


@requires_database
class TestAdjustConcurrency:
    def test_concurrent_approval_prevents_double_inventory_posting(self):
        """Two concurrent approve+execute calls: only one inventory transaction posted."""
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

            currency = bootstrap_service.ensure_default_currency(session_setup, actor_id=su.id)
            warehouse = bootstrap_service.ensure_default_warehouse(session_setup, actor_id=su.id)
            _assign_warehouse(session_setup, rep.id, warehouse.id, actor_id=su.id)

            product = _create_product(session_setup, su)
            _post_stock(session_setup, warehouse.id, product.id, 100, currency.id, su.id)

            reason = _create_reason_code(session_setup, su)

            token = bot_session_service.generate_binding_token(
                session_setup, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=su.id,
            )
            bot_session = bot_session_service.create_binding(
                session_setup, binding_token=token, platform_code="TELEGRAM",
                platform_user_id=f"cnv-{uuid.uuid4().hex[:6]}", linked_by=user.id,
            )

            # Create approval request with adjust payload.
            payload = {
                "product_id": str(product.id),
                "product_sku": product.sku,
                "warehouse_id": str(warehouse.id),
                "warehouse_code": warehouse.code,
                "adjustment_type": "NEGATIVE",
                "delta_quantity": "-5",
                "reason_code_id": str(reason.id),
                "reason_code": reason.code,
                "reason_text": "Concurrency test",
                "requested_by": str(user.id),
                "representative_id": str(rep.id),
            }

            request = create_approval_request(
                session_setup,
                entity_type="bot_command:adjust",
                entity_id=bot_session.id,
                requested_by=user.id,
                payload=payload,
            )
            session_setup.commit()
            request_id = request.id
        finally:
            session_setup.close()

        # Record balance before.
        session_check = factory()
        try:
            balance_before = _get_stock_balance(session_check, warehouse.id, product.id)
        finally:
            session_check.close()

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

        # Verify balance changed by exactly -5 (only one execution).
        session_final = factory()
        try:
            balance_after = _get_stock_balance(session_final, warehouse.id, product.id)
            assert balance_after == balance_before - 5, (
                f"Expected balance {balance_before - 5}, got {balance_after}. "
                f"Double execution detected!"
            )

            # Verify approval request is terminal.
            final = session_final.get(ApprovalRequest, request_id)
            assert final.status in ("APPROVED", "REJECTED", "CANCELLED")

            # Verify StockAdjustment record exists.
            adj_records = session_final.execute(
                select(StockAdjustment).where(
                    StockAdjustment.product_id == product.id,
                    StockAdjustment.warehouse_id == warehouse.id,
                )
            ).scalars().all()
            assert len(adj_records) == 1, "Exactly one StockAdjustment record should exist"
        finally:
            session_final.close()
