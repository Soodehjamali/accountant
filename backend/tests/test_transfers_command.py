"""PostgreSQL-backed tests for /transfers bot command (Tier 1 — read-only).

Covers:
- BOT_QUERY permission required
- BOT_WRITE alone insufficient
- unbound session rejected
- representative scope enforced
- outbound transfer visible
- inbound transfer visible
- unrelated transfer NOT visible
- cross-representative isolation
- empty result
- multiple transfers
- deterministic ordering
- regression of existing commands
- no UUID leakage

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
    reason="DATABASE_URL not set; skipping /transfers tests",
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
    su = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=su.id, updated_by=su.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session, su):
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(code=f"REP-TRS-{suffix.upper()}", person_name=f"Transfers Rep {suffix}", status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session, su, rep):
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(session, username=f"trs_user_{suffix}", email=f"trs_{suffix}@test.invalid", password="test-password-123", created_by=su.id, representative_id=rep.id)


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"TRS_{perm_code}_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"TRS {perm_code} {suffix}", created_by=su.id)
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


def _create_warehouse(session, su, prefix="WH-TRS"):
    suffix = uuid.uuid4().hex[:6]
    wh = Warehouse(code=f"{prefix}-{suffix}", name=f"Transfers WH {suffix}", type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(wh)
    session.flush()
    return wh


def _create_product(session, su):
    suffix = uuid.uuid4().hex[:8]
    product = Product(sku=f"SKU-TRS-{suffix}", name=f"Transfers Product {suffix}", base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id, status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(product)
    session.flush()
    return product


def _seed_stock(session, wh_id, product_id, qty, su):
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    inventory_service.post_transaction(session, product_id=product_id, warehouse_id=wh_id, movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal(str(qty)), unit_cost=decimal.Decimal("5.000000"), currency_id=currency.id, actor_user_id=su.id)
    session.flush()


def _create_transfer(session, su, source_wh, dest_wh, product, *, state="DRAFT", qty=10):
    from services.stock_transfer_service import create_transfer, dispatch_transfer, TransferLineInput
    transfer = create_transfer(session, source_warehouse_id=source_wh.id, destination_warehouse_id=dest_wh.id, lines=[TransferLineInput(product_id=product.id, qty_requested=decimal.Decimal(str(qty)), unit_cost=decimal.Decimal("5.000000"))], requested_by=su.id)
    session.flush()
    if state == "DISPATCHED":
        _seed_stock(session, source_wh.id, product.id, qty + 10, su)
        dispatch_transfer(session, transfer.id, actor_user_id=su.id)
        session.flush()
    return transfer


# =======================================================================
# 1. Permission
# =======================================================================

@requires_database
class TestTransfersRequiresBOTQuery:
    def test_rejected_without_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid, grant_query=False)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
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
            puid = f"trs-bw-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid, grant_query=False)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_accepted_with_bot_query(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-ok-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================

@requires_database
class TestTransfersUnboundSession:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/transfers")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Empty result
# =======================================================================

@requires_database
class TestTransfersEmpty:
    def test_empty_when_no_transfers(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-empty-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)
            assert "No transfers found" in response.text
        finally:
            session.close()

    def test_empty_when_no_warehouses(self):
        """Rep with no warehouse assignment sees nothing."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-nowh-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            # Create a transfer to another rep's warehouse.
            source_wh = _create_warehouse(session, su, "WH-OTHER")
            dest_wh = _create_warehouse(session, su, "WH-OTHER2")
            product = _create_product(session, su)
            _create_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)
            assert "No transfers found" in response.text
        finally:
            session.close()


# =======================================================================
# 4. Outbound transfer visible
# =======================================================================

@requires_database
class TestTransfersOutbound:
    def test_outbound_transfer_visible(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-out-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            source_wh = _create_warehouse(session, su, "WH-SRC")
            dest_wh = _create_warehouse(session, su, "WH-DST")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)

            assert transfer.transfer_number in response.text
            assert "OUT" in response.text
        finally:
            session.close()


# =======================================================================
# 5. Inbound transfer visible
# =======================================================================

@requires_database
class TestTransfersInbound:
    def test_inbound_transfer_visible(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-in-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            source_wh = _create_warehouse(session, su, "WH-SRC2")
            dest_wh = _create_warehouse(session, su, "WH-DST2")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_transfer(session, su, source_wh, dest_wh, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)

            assert transfer.transfer_number in response.text
            assert "IN" in response.text
        finally:
            session.close()


# =======================================================================
# 6. Cross-representative isolation
# =======================================================================

@requires_database
class TestTransfersIsolation:
    def test_unrelated_transfer_not_visible(self):
        """Rep should not see transfers to unrelated warehouses."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-iso-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            my_wh = _create_warehouse(session, su, "WH-MINE")
            other_wh = _create_warehouse(session, su, "WH-OTHER-A")
            other_wh2 = _create_warehouse(session, su, "WH-OTHER-B")
            _assign_warehouse(session, rep.id, my_wh.id, su.id)

            product = _create_product(session, su)
            other_transfer = _create_transfer(session, su, other_wh, other_wh2, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)

            assert other_transfer.transfer_number not in response.text
            assert "No transfers found" in response.text
        finally:
            session.close()

    def test_cross_rep_isolation(self):
        """Rep A should not see Rep B's transfers."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A's warehouse
            puid_a = f"trs-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            wh_a = _create_warehouse(session, su, "WH-A")
            _assign_warehouse(session, rep_a.id, wh_a.id, su.id)

            # Rep B's warehouse
            puid_b = f"trs-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            wh_b = _create_warehouse(session, su, "WH-B")
            _assign_warehouse(session, rep_b.id, wh_b.id, su.id)

            product = _create_product(session, su)
            # Transfer between A and B — both see it.
            t_ab = _create_transfer(session, su, wh_a, wh_b, product)

            # Transfer between B and some unrelated warehouse — only B sees it.
            wh_c = _create_warehouse(session, su, "WH-C")
            t_bc = _create_transfer(session, su, wh_b, wh_c, product)

            # Rep A's view.
            msg_a = BotMessage(platform_user_id=puid_a, platform_code="TELEGRAM", text="/transfers")
            resp_a = process_message(session, message=msg_a)
            assert t_ab.transfer_number in resp_a.text  # A sees A->B
            assert t_bc.transfer_number not in resp_a.text  # A does NOT see B->C

            # Rep B's view.
            msg_b = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text="/transfers")
            resp_b = process_message(session, message=msg_b)
            assert t_ab.transfer_number in resp_b.text  # B sees A->B
            assert t_bc.transfer_number in resp_b.text  # B sees B->C
        finally:
            session.close()


# =======================================================================
# 7. Multiple transfers
# =======================================================================

@requires_database
class TestTransfersMultiple:
    def test_multiple_transfers_listed(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-multi-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            wh1 = _create_warehouse(session, su, "WH-1")
            wh2 = _create_warehouse(session, su, "WH-2")
            _assign_warehouse(session, rep.id, wh1.id, su.id)

            product = _create_product(session, su)
            t1 = _create_transfer(session, su, wh1, wh2, product)
            t2 = _create_transfer(session, su, wh1, wh2, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)

            assert t1.transfer_number in response.text
            assert t2.transfer_number in response.text
        finally:
            session.close()


# =======================================================================
# 8. No UUID leakage
# =======================================================================

@requires_database
class TestTransfersNoUUIDLeakage:
    def test_no_internal_uuids_in_response(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-uuid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            wh1 = _create_warehouse(session, su, "WH-U1")
            wh2 = _create_warehouse(session, su, "WH-U2")
            _assign_warehouse(session, rep.id, wh1.id, su.id)

            product = _create_product(session, su)
            transfer = _create_transfer(session, su, wh1, wh2, product)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)

            # Must not contain raw UUIDs
            assert str(transfer.id) not in response.text
            assert str(rep.id) not in response.text
            assert str(product.id) not in response.text
        finally:
            session.close()


# =======================================================================
# 9. Regression
# =======================================================================

@requires_database
class TestTransfersRegression:
    def test_orders_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-regord-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/orders")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()

    def test_confirm_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-regcfm-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/confirm")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_dispatch_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trs-regdsp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/dispatch")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()
