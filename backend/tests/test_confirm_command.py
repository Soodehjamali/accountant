"""PostgreSQL-backed tests for /confirm bot command (Tier 2 — direct write).

Covers:
- Authorization: BOT_WRITE required, unbound session, representative identity
- Validation: missing args, nonexistent transfer, wrong state, wrong warehouse
- Scope: own warehouse succeeds, cross-rep warehouse rejected
- Idempotency: already-confirmed transfer rejected
- Audit: transfer_history recorded, inventory mutation audited
- Regression: existing commands still work

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
from database.models.bot_platform_ref import BotPlatformRef
from database.models.product import Product
from database.models.representative import Representative
from database.models.stock_transfer import StockTransfer
from database.models.transfer_history import TransferHistory
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

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping /confirm tests",
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
        code=f"REP-CFM-{suffix.upper()}",
        person_name=f"Confirm Rep {suffix}",
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
        username=f"cfm_user_{suffix}",
        email=f"cfm_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_permission(session: Session, app_user: AppUser, system_user, perm_code: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"CFM_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"CFM {perm_code} {suffix}",
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


def _create_warehouse(session, system_user, code_prefix: str = "WH-CFM") -> Warehouse:
    suffix = uuid.uuid4().hex[:6]
    wh = Warehouse(
        code=f"{code_prefix}-{suffix}",
        name=f"Confirm WH {suffix}",
        type="REPRESENTATIVE",
        ownership_mode="OWNED",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(wh)
    session.flush()
    return wh


def _create_product(session, system_user) -> Product:
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-CFM-{suffix}",
        name=f"Confirm Product {suffix}",
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


def _create_dispatched_transfer(session, system_user, source_wh, dest_wh, product, *, rep=None, qty: int = 10) -> StockTransfer:
    """Create a DISPATCHED transfer from source_wh to dest_wh."""
    from services.stock_transfer_service import create_transfer, dispatch_transfer, TransferLineInput

    # Seed stock at source warehouse to avoid NegativeStockError.
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=source_wh.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty + 10)),
        unit_cost=decimal.Decimal("5.000000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()

    transfer = create_transfer(
        session,
        source_warehouse_id=source_wh.id,
        destination_warehouse_id=dest_wh.id,
        lines=[TransferLineInput(
            product_id=product.id,
            qty_requested=decimal.Decimal(str(qty)),
            unit_cost=decimal.Decimal("5.000000"),
        )],
        requested_by=system_user.id,
    )
    session.flush()

    # Dispatch it.
    dispatch_transfer(
        session, transfer.id,
        actor_user_id=system_user.id,
    )
    session.flush()
    return transfer


# =======================================================================
# 1. Authorization: BOT_WRITE required
# =======================================================================

@requires_database
class TestConfirmRequiresBOTWrite:
    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/confirm TRF-00000000-TEST",
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
            puid = f"cfm2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/confirm",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================

@requires_database
class TestConfirmUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999", platform_code="TELEGRAM",
                text="/confirm TRF-00000000-TEST",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Validation
# =======================================================================

@requires_database
class TestConfirmValidation:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-ma-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/confirm")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_nonexistent_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/confirm TRF-00000000-NONEXISTENT",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_wrong_state_rejected(self):
        """A DRAFT transfer cannot be confirmed."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-ws-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            # Create source/dest warehouses, assign dest to rep.
            source_wh = _create_warehouse(session, su, "WH-SRC")
            dest_wh = _create_warehouse(session, su, "WH-DST")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)

            from services.stock_transfer_service import create_transfer, TransferLineInput
            transfer = create_transfer(
                session,
                source_warehouse_id=source_wh.id,
                destination_warehouse_id=dest_wh.id,
                lines=[TransferLineInput(
                    product_id=product.id,
                    qty_requested=decimal.Decimal("5"),
                    unit_cost=decimal.Decimal("10.000000"),
                )],
                requested_by=su.id,
            )
            session.flush()

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            response = process_message(session, message=msg)
            assert "cannot be confirmed" in response.text.lower()
            assert "DRAFT" in response.text
        finally:
            session.close()

    def test_wrong_warehouse_rejected(self):
        """A transfer to a warehouse not assigned to the rep is rejected."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-wh-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC2")
            dest_wh = _create_warehouse(session, su, "WH-DST2")
            # Do NOT assign dest_wh to rep.

            product = _create_product(session, su)
            transfer = _create_dispatched_transfer(
                session, su, source_wh, dest_wh, product,
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            response = process_message(session, message=msg)
            assert "access denied" in response.text.lower() or "not destined" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 4. Valid confirmation
# =======================================================================

@requires_database
class TestConfirmValid:
    def test_confirm_own_warehouse_succeeds(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC3")
            dest_wh = _create_warehouse(session, su, "WH-DST3")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_dispatched_transfer(
                session, su, source_wh, dest_wh, product,
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "confirmed" in response.text.lower()
            assert transfer.transfer_number in response.text

            # Verify state is RECEIVED.
            refreshed = session.get(StockTransfer, transfer.id)
            assert refreshed.state == "RECEIVED"
            assert refreshed.received_at is not None
        finally:
            session.close()

    def test_confirm_inventory_posted(self):
        """Confirming posts TRANSFER_IN to destination warehouse."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC4")
            dest_wh = _create_warehouse(session, su, "WH-DST4")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_dispatched_transfer(
                session, su, source_wh, dest_wh, product,
            )

            # Check dest balance before confirm.
            from services.inventory_service import get_balance
            dest_before = get_balance(session, warehouse_id=dest_wh.id, product_id=product.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            process_message(session, message=msg)

            # Check dest balance after confirm.
            dest_after = get_balance(session, warehouse_id=dest_wh.id, product_id=product.id)
            assert dest_after == dest_before + decimal.Decimal("10"), (
                f"Expected dest balance {dest_before + 10}, got {dest_after}"
            )
        finally:
            session.close()


# =======================================================================
# 5. Idempotency: already received
# =======================================================================

@requires_database
class TestConfirmIdempotency:
    def test_already_received_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-idem-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC5")
            dest_wh = _create_warehouse(session, su, "WH-DST5")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_dispatched_transfer(
                session, su, source_wh, dest_wh, product,
            )

            # First confirm succeeds.
            msg1 = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            process_message(session, message=msg1)

            # Second confirm fails.
            msg2 = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            response = process_message(session, message=msg2)
            assert "cannot be confirmed" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Cross-representative isolation
# =======================================================================

@requires_database
class TestConfirmCrossRepIsolation:
    def test_rep_cannot_confirm_other_reps_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A's warehouse (source)
            source_wh = _create_warehouse(session, su, "WH-SRC6")
            dest_wh = _create_warehouse(session, su, "WH-DST6")

            # Rep A owns dest_wh
            puid_a = f"cfm-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            _grant_bot_write(session, user_a, su)
            _assign_warehouse(session, rep_a.id, dest_wh.id, su.id)

            # Rep B does NOT own dest_wh
            puid_b = f"cfm-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_bot_write(session, user_b, su)

            product = _create_product(session, su)
            transfer = _create_dispatched_transfer(
                session, su, source_wh, dest_wh, product,
            )

            # Rep B tries to confirm — should fail.
            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            response_b = process_message(session, message=msg_b)
            assert "access denied" in response_b.text.lower() or "not destined" in response_b.text.lower()
        finally:
            session.close()


# =======================================================================
# 7. Audit
# =======================================================================

@requires_database
class TestConfirmAudit:
    def test_transfer_history_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-aud-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC7")
            dest_wh = _create_warehouse(session, su, "WH-DST7")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_dispatched_transfer(
                session, su, source_wh, dest_wh, product,
            )

            # History before confirm: DRAFT->DRAFT (creation) + DRAFT->DISPATCHED
            history_before = session.execute(
                select(TransferHistory).where(
                    TransferHistory.stock_transfer_id == transfer.id,
                ).order_by(TransferHistory.event_at)
            ).scalars().all()
            assert len(history_before) == 2

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/confirm {transfer.transfer_number}",
            )
            process_message(session, message=msg)

            # History after confirm: should have DISPATCHED->RECEIVED
            history_after = session.execute(
                select(TransferHistory).where(
                    TransferHistory.stock_transfer_id == transfer.id,
                ).order_by(TransferHistory.event_at)
            ).scalars().all()
            assert len(history_after) == 3
            assert history_after[2].from_state == "DISPATCHED"
            assert history_after[2].to_state == "RECEIVED"
        finally:
            session.close()


# =======================================================================
# 8. Regression
# =======================================================================

@requires_database
class TestConfirmRegression:
    def test_read_commands_still_work(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-reg-{uuid.uuid4().hex[:6]}"
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
            puid = f"cfm-regco-{uuid.uuid4().hex[:6]}"
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
            puid = f"cfm-regadj-{uuid.uuid4().hex[:6]}"
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
            puid = f"cfm-regret-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/return")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_pending_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"cfm-regpend-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            from services.bot_command_service import APPROVE_PERMISSION
            _grant_permission(session, user, su, APPROVE_PERMISSION)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/pending")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()
