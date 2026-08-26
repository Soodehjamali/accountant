"""PostgreSQL-backed tests for /dispatch bot command (Tier 2 — direct write).

Covers:
- Authorization: BOT_WRITE required, unbound session, representative identity
- Validation: missing args, nonexistent transfer, wrong state, wrong warehouse
- Scope: own warehouse succeeds, cross-rep warehouse rejected
- Idempotency: already-dispatched transfer rejected
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
    reason="DATABASE_URL not set; skipping /dispatch tests",
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
    p = BotPlatformRef(code="TELEGRAM", created_by=system_user.id, updated_by=system_user.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session: Session, system_user) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-DSP-{suffix.upper()}",
        person_name=f"Dispatch Rep {suffix}",
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
        username=f"dsp_user_{suffix}",
        email=f"dsp_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_permission(session, app_user, system_user, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"DSP_{perm_code}_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"DSP {perm_code} {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(session, code=perm_code, name=f"Permission {perm_code}", resource="bot", action=perm_code.lower(), created_by=system_user.id)
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=perm_code)
    rbac_service.assign_role(session, user_id=app_user.id, role_code=role_code, assigned_by=system_user.id)


def _grant_bot_query(session, app_user, system_user):
    _grant_permission(session, app_user, system_user, BOT_QUERY_PERMISSION)


def _grant_bot_write(session, app_user, system_user):
    _grant_permission(session, app_user, system_user, BOT_WRITE_PERMISSION)


def _make_bound_session(session, system_user, *, platform_user_id):
    rep = _create_representative(session, system_user)
    app_user = _create_app_user(session, system_user, rep)
    _grant_bot_query(session, app_user, system_user)
    _ensure_telegram_platform(session)
    token = bot_session_service.generate_binding_token(session, representative_id=rep.id, platform_code="TELEGRAM", created_by=system_user.id)
    bot_session = bot_session_service.create_binding(session, binding_token=token, platform_code="TELEGRAM", platform_user_id=platform_user_id, linked_by=app_user.id)
    return rep, app_user, bot_session


def _assign_warehouse(session, rep_id, warehouse_id, actor_id, *, is_primary=True):
    from datetime import datetime, timezone, timedelta
    session.add(WarehouseAssignment(
        representative_id=rep_id, warehouse_id=warehouse_id,
        is_primary=is_primary,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        created_by=actor_id, updated_by=actor_id,
    ))
    session.flush()


def _create_warehouse(session, system_user, prefix="WH-DSP"):
    suffix = uuid.uuid4().hex[:6]
    wh = Warehouse(code=f"{prefix}-{suffix}", name=f"Dispatch WH {suffix}", type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE", created_by=system_user.id, updated_by=system_user.id)
    session.add(wh)
    session.flush()
    return wh


def _create_product(session, system_user):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-DSP-{suffix}", name=f"Dispatch Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=system_user.id).id,
        status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
    )
    session.add(product)
    session.flush()
    return product


def _seed_stock(session, warehouse_id, product_id, qty, system_user):
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    inventory_service.post_transaction(
        session, product_id=product_id, warehouse_id=warehouse_id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty)),
        unit_cost=decimal.Decimal("5.000000"), currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()


def _create_draft_transfer(session, system_user, source_wh, dest_wh, product, *, qty=10):
    from services.stock_transfer_service import create_transfer, TransferLineInput
    transfer = create_transfer(
        session,
        source_warehouse_id=source_wh.id,
        destination_warehouse_id=dest_wh.id,
        lines=[TransferLineInput(product_id=product.id, qty_requested=decimal.Decimal(str(qty)), unit_cost=decimal.Decimal("5.000000"))],
        requested_by=system_user.id,
    )
    session.flush()
    return transfer


# =======================================================================
# 1. Authorization: BOT_WRITE required
# =======================================================================

@requires_database
class TestDispatchRequiresBOTWrite:
    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/dispatch TRF-TEST")
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
            puid = f"dsp2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/dispatch")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================

@requires_database
class TestDispatchUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/dispatch TRF-TEST")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Validation
# =======================================================================

@requires_database
class TestDispatchValidation:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-ma-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/dispatch")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_nonexistent_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/dispatch TRF-00000000-NONEXISTENT")
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_wrong_state_rejected(self):
        """A DISPATCHED transfer cannot be re-dispatched."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-ws-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC")
            dest_wh = _create_warehouse(session, su, "WH-DST")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            _seed_stock(session, source_wh.id, product.id, 50, su)

            from services.stock_transfer_service import dispatch_transfer
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)
            dispatch_transfer(session, transfer.id, actor_user_id=su.id)
            session.flush()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            response = process_message(session, message=msg)
            assert "cannot be dispatched" in response.text.lower()
        finally:
            session.close()

    def test_wrong_warehouse_rejected(self):
        """A transfer from a warehouse not assigned to the rep is rejected."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-wh-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC2")
            dest_wh = _create_warehouse(session, su, "WH-DST2")
            # Do NOT assign source_wh to rep.

            product = _create_product(session, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            response = process_message(session, message=msg)
            assert "access denied" in response.text.lower() or "not originate" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 4. Valid dispatch
# =======================================================================

@requires_database
class TestDispatchValid:
    def test_dispatch_own_warehouse_succeeds(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC3")
            dest_wh = _create_warehouse(session, su, "WH-DST3")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            _seed_stock(session, source_wh.id, product.id, 50, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "dispatched" in response.text.lower()
            assert transfer.transfer_number in response.text

            refreshed = session.get(StockTransfer, transfer.id)
            assert refreshed.state == "DISPATCHED"
            assert refreshed.dispatched_at is not None
        finally:
            session.close()

    def test_dispatch_inventory_posted(self):
        """Dispatching posts TRANSFER_OUT at source warehouse."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC4")
            dest_wh = _create_warehouse(session, su, "WH-DST4")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            _seed_stock(session, source_wh.id, product.id, 50, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            from services.inventory_service import get_balance
            src_before = get_balance(session, warehouse_id=source_wh.id, product_id=product.id)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            process_message(session, message=msg)

            src_after = get_balance(session, warehouse_id=source_wh.id, product_id=product.id)
            assert src_after == src_before - decimal.Decimal("10"), (
                f"Expected src balance {src_before - 10}, got {src_after}"
            )
        finally:
            session.close()


# =======================================================================
# 5. Idempotency: already dispatched
# =======================================================================

@requires_database
class TestDispatchIdempotency:
    def test_already_dispatched_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-idem-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC5")
            dest_wh = _create_warehouse(session, su, "WH-DST5")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            _seed_stock(session, source_wh.id, product.id, 50, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            msg1 = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            process_message(session, message=msg1)

            msg2 = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            response = process_message(session, message=msg2)
            assert "cannot be dispatched" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Cross-representative isolation
# =======================================================================

@requires_database
class TestDispatchCrossRepIsolation:
    def test_rep_cannot_dispatch_other_reps_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            source_wh = _create_warehouse(session, su, "WH-SRC6")
            dest_wh = _create_warehouse(session, su, "WH-DST6")

            # Rep A owns source_wh
            puid_a = f"dsp-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            _grant_bot_write(session, user_a, su)
            _assign_warehouse(session, rep_a.id, source_wh.id, su.id)

            # Rep B does NOT own source_wh
            puid_b = f"dsp-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_bot_write(session, user_b, su)

            product = _create_product(session, su)
            _seed_stock(session, source_wh.id, product.id, 50, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            response_b = process_message(session, message=msg_b)
            assert "access denied" in response_b.text.lower() or "not originate" in response_b.text.lower()
        finally:
            session.close()


# =======================================================================
# 7. Audit
# =======================================================================

@requires_database
class TestDispatchAudit:
    def test_transfer_history_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-aud-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC7")
            dest_wh = _create_warehouse(session, su, "WH-DST7")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            _seed_stock(session, source_wh.id, product.id, 50, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            # History before: DRAFT->DRAFT (creation)
            history_before = session.execute(
                select(TransferHistory).where(TransferHistory.stock_transfer_id == transfer.id).order_by(TransferHistory.event_at)
            ).scalars().all()
            assert len(history_before) == 1

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/dispatch {transfer.transfer_number}")
            process_message(session, message=msg)

            history_after = session.execute(
                select(TransferHistory).where(TransferHistory.stock_transfer_id == transfer.id).order_by(TransferHistory.event_at)
            ).scalars().all()
            assert len(history_after) == 2
            assert history_after[1].from_state == "DRAFT"
            assert history_after[1].to_state == "DISPATCHED"
        finally:
            session.close()


# =======================================================================
# 8. Regression
# =======================================================================

@requires_database
class TestDispatchRegression:
    def test_read_commands_still_work(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-reg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/me")
            response = process_message(session, message=msg)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_confirm_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-regcfm-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/confirm")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_create_order_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"dsp-regco-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-order")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()
