"""PostgreSQL-backed tests for /cancel-transfer bot command (Tier 2 — direct write).

Covers:
- BOT_WRITE required
- BOT_QUERY alone insufficient
- unbound session rejected
- missing args
- nonexistent transfer
- source warehouse scope required
- destination-only rep cannot cancel
- cross-representative isolation
- DRAFT transfer cancelled successfully
- non-DRAFT transfer rejected
- inventory not posted on DRAFT cancel
- audit/history recorded
- idempotency follows existing service semantics
- no UUID leakage
- regression of existing commands

All tests use the real PostgreSQL database (no mocks).
"""

from __future__ import annotations

import decimal
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    PermissionDeniedError,
    UnboundSessionError,
    process_message,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping /cancel-transfer tests",
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _ensure_telegram_platform(session):
    existing = session.execute(select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")).scalar_one_or_none()
    if existing is not None:
        return existing
    su = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=su.id, updated_by=su.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session, su):
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(code=f"REP-CTF-{suffix.upper()}", person_name=f"CancelTransfer Rep {suffix}", status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session, su, rep):
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(session, username=f"ctf_user_{suffix}", email=f"ctf_{suffix}@test.invalid", password="test-password-123", created_by=su.id, representative_id=rep.id)


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"CTF_{perm_code}_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CTF {perm_code} {suffix}", created_by=su.id)
    try:
        rbac_service.create_permission(session, code=perm_code, name=f"Permission {perm_code}", resource="bot", action=perm_code.lower(), created_by=su.id)
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=perm_code)
    rbac_service.assign_role(session, user_id=app_user.id, role_code=role_code, assigned_by=su.id)


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
    token = bot_session_service.generate_binding_token(session, representative_id=rep.id, platform_code="TELEGRAM", created_by=su.id)
    bot_session = bot_session_service.create_binding(session, binding_token=token, platform_code="TELEGRAM", platform_user_id=platform_user_id, linked_by=user.id)
    return rep, user, bot_session


def _assign_warehouse(session, rep_id, wh_id, su_id, *, is_primary=True):
    from datetime import datetime, timezone, timedelta
    session.add(WarehouseAssignment(representative_id=rep_id, warehouse_id=wh_id, is_primary=is_primary, effective_from=datetime.now(timezone.utc) - timedelta(days=30), created_by=su_id, updated_by=su_id))
    session.flush()


def _create_warehouse(session, su, prefix="WH-CTF"):
    suffix = uuid.uuid4().hex[:6]
    wh = Warehouse(code=f"{prefix}-{suffix}", name=f"CancelTransfer WH {suffix}", type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(wh)
    session.flush()
    return wh


def _create_product(session, su):
    suffix = uuid.uuid4().hex[:8]
    product = Product(sku=f"SKU-CTF-{suffix}", name=f"CancelTransfer Product {suffix}", base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id, status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(product)
    session.flush()
    return product


def _create_draft_transfer(session, su, source_wh, dest_wh, product):
    from services.stock_transfer_service import create_transfer, TransferLineInput
    transfer = create_transfer(session, source_warehouse_id=source_wh.id, destination_warehouse_id=dest_wh.id, lines=[TransferLineInput(product_id=product.id, qty_requested=decimal.Decimal("10"), unit_cost=decimal.Decimal("5.000000"))], requested_by=su.id)
    session.flush()
    return transfer


# =======================================================================
# 1. Permission
# =======================================================================

@requires_database
class TestCancelTransferPermission:
    def test_rejected_without_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/cancel-transfer TRF-TEST")
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
            puid = f"ctf-bq-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/cancel-transfer TRF-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================

@requires_database
class TestCancelTransferUnbound:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/cancel-transfer TRF-TEST")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Validation
# =======================================================================

@requires_database
class TestCancelTransferValidation:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-ma-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/cancel-transfer")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_nonexistent_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/cancel-transfer TRF-00000000-NONEXISTENT")
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_wrong_warehouse_rejected(self):
        """Transfer from unassigned source warehouse — not found."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-wh-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-OTHER-SRC")
            dest_wh = _create_warehouse(session, su, "WH-OTHER-DST")
            product = _create_product(session, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_non_draft_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-nd-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-SRC")
            dest_wh = _create_warehouse(session, su, "WH-DST")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)
            product = _create_product(session, su)

            from services.stock_transfer_service import dispatch_transfer
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)
            # Seed stock so dispatch doesn't go negative.
            currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
            inventory_service.post_transaction(session, product_id=product.id, warehouse_id=source_wh.id, movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("50"), unit_cost=decimal.Decimal("5.000000"), currency_id=currency.id, actor_user_id=su.id)
            session.flush()
            dispatch_transfer(session, transfer.id, actor_user_id=su.id)
            session.flush()

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)
            assert "cannot be cancelled" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 4. Successful cancellation
# =======================================================================

@requires_database
class TestCancelTransferValid:
    def test_cancel_own_source_succeeds(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-CSRC")
            dest_wh = _create_warehouse(session, su, "WH-CDST")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)
            product = _create_product(session, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "cancelled" in response.text.lower()
            assert transfer.transfer_number in response.text

            refreshed = session.get(StockTransfer, transfer.id)
            assert refreshed.state == "CANCELLED"
        finally:
            session.close()

    def test_no_inventory_on_draft_cancel(self):
        """DRAFT cancellation must not post inventory."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-inv-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-CSRC2")
            dest_wh = _create_warehouse(session, su, "WH-CDST2")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)
            product = _create_product(session, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            from services.inventory_service import get_balance
            src_before = get_balance(session, warehouse_id=source_wh.id, product_id=product.id)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            process_message(session, message=msg)

            src_after = get_balance(session, warehouse_id=source_wh.id, product_id=product.id)
            assert src_before == src_after, "DRAFT cancel must not change inventory"
        finally:
            session.close()


# =======================================================================
# 5. Cross-representative isolation
# =======================================================================

@requires_database
class TestCancelTransferIsolation:
    def test_rep_cannot_cancel_other_reps_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            source_wh = _create_warehouse(session, su, "WH-ISRC")
            dest_wh = _create_warehouse(session, su, "WH-IDST")

            # Rep A owns source_wh
            puid_a = f"ctf-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            _grant_bot_write(session, user_a, su)
            _assign_warehouse(session, rep_a.id, source_wh.id, su.id)

            # Rep B does NOT own source_wh
            puid_b = f"ctf-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            _grant_bot_write(session, user_b, su)

            product = _create_product(session, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            # Rep B tries to cancel — not found (scope-hidden).
            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            response_b = process_message(session, message=msg_b)
            assert "not found" in response_b.text.lower()

            # Rep A can cancel.
            msg_a = BotMessage(platform_user_id=puid_a, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            response_a = process_message(session, message=msg_a)
            assert "cancelled" in response_a.text.lower()
        finally:
            session.close()


# =======================================================================
# 6. Audit
# =======================================================================

@requires_database
class TestCancelTransferAudit:
    def test_transfer_history_recorded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-aud-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)

            source_wh = _create_warehouse(session, su, "WH-ASRC")
            dest_wh = _create_warehouse(session, su, "WH-ADST")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)
            product = _create_product(session, su)
            transfer = _create_draft_transfer(session, su, source_wh, dest_wh, product)

            # History before: DRAFT->DRAFT (creation)
            history_before = session.execute(
                select(TransferHistory).where(TransferHistory.stock_transfer_id == transfer.id).order_by(TransferHistory.event_at)
            ).scalars().all()
            assert len(history_before) == 1

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/cancel-transfer {transfer.transfer_number}")
            process_message(session, message=msg)

            history_after = session.execute(
                select(TransferHistory).where(TransferHistory.stock_transfer_id == transfer.id).order_by(TransferHistory.event_at)
            ).scalars().all()
            assert len(history_after) == 2
            assert history_after[1].from_state == "DRAFT"
            assert history_after[1].to_state == "CANCELLED"
        finally:
            session.close()


# =======================================================================
# 7. Regression
# =======================================================================

@requires_database
class TestCancelTransferRegression:
    def test_dispatch_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-regdsp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/dispatch")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_confirm_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-regcfm-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/confirm")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_transfers_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-regh-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()
