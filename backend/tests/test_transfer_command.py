"""PostgreSQL-backed tests for /transfer <transfer_number> detail command.

Covers:
- BOT_QUERY required
- BOT_WRITE alone insufficient
- unbound session rejected
- missing arguments
- nonexistent transfer
- visible outbound transfer
- visible inbound transfer
- out-of-scope transfer hidden
- cross-representative isolation
- exact transfer details returned
- transfer lines returned
- status displayed correctly
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
    reason="DATABASE_URL not set; skipping /transfer tests",
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
    rep = Representative(code=f"REP-TRD-{suffix.upper()}", person_name=f"TransferDetail Rep {suffix}", status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session, su, rep):
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(session, username=f"trd_user_{suffix}", email=f"trd_{suffix}@test.invalid", password="test-password-123", created_by=su.id, representative_id=rep.id)


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"TRD_{perm_code}_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"TRD {perm_code} {suffix}", created_by=su.id)
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


def _create_warehouse(session, su, prefix="WH-TRD"):
    suffix = uuid.uuid4().hex[:6]
    wh = Warehouse(code=f"{prefix}-{suffix}", name=f"TransferDetail WH {suffix}", type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(wh)
    session.flush()
    return wh


def _create_product(session, su):
    suffix = uuid.uuid4().hex[:8]
    product = Product(sku=f"SKU-TRD-{suffix}", name=f"TransferDetail Product {suffix}", base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id, status="ACTIVE", created_by=su.id, updated_by=su.id)
    session.add(product)
    session.flush()
    return product


def _seed_stock(session, wh_id, product_id, qty, su):
    currency = bootstrap_service.ensure_default_currency(session, actor_id=su.id)
    inventory_service.post_transaction(session, product_id=product_id, warehouse_id=wh_id, movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal(str(qty)), unit_cost=decimal.Decimal("5.000000"), currency_id=currency.id, actor_user_id=su.id)
    session.flush()


def _create_transfer_with_lines(session, su, source_wh, dest_wh, products, *, qty=10):
    """Create a DRAFT transfer with multiple product lines."""
    from services.stock_transfer_service import create_transfer, TransferLineInput
    lines = [TransferLineInput(product_id=p.id, qty_requested=decimal.Decimal(str(qty)), unit_cost=decimal.Decimal("5.000000")) for p in products]
    transfer = create_transfer(session, source_warehouse_id=source_wh.id, destination_warehouse_id=dest_wh.id, lines=lines, requested_by=su.id)
    session.flush()
    return transfer


# =======================================================================
# 1. Permission
# =======================================================================

@requires_database
class TestTransferDetailPermission:
    def test_rejected_without_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid, grant_query=False)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfer TRF-TEST")
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
            puid = f"trd-bw-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid, grant_query=False)
            _grant_bot_write(session, user, su)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfer TRF-TEST")
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 2. Unbound session
# =======================================================================

@requires_database
class TestTransferDetailUnbound:
    def test_unbound_session_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(platform_user_id="99999", platform_code="TELEGRAM", text="/transfer TRF-TEST")
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 3. Missing/invalid arguments
# =======================================================================

@requires_database
class TestTransferDetailArgs:
    def test_missing_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-ma-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfer")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_nonexistent_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-ne-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfer TRF-00000000-NONEXISTENT")
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()


# =======================================================================
# 4. Outbound transfer visible
# =======================================================================

@requires_database
class TestTransferDetailOutbound:
    def test_outbound_transfer_shows_detail(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-out-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            source_wh = _create_warehouse(session, su, "WH-SRC")
            dest_wh = _create_warehouse(session, su, "WH-DST")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_transfer_with_lines(session, su, source_wh, dest_wh, [product])

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)

            assert transfer.transfer_number in response.text
            assert "OUTBOUND" in response.text
            assert "DRAFT" in response.text
            assert product.sku in response.text
        finally:
            session.close()


# =======================================================================
# 5. Inbound transfer visible
# =======================================================================

@requires_database
class TestTransferDetailInbound:
    def test_inbound_transfer_shows_detail(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-in-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            source_wh = _create_warehouse(session, su, "WH-SRC2")
            dest_wh = _create_warehouse(session, su, "WH-DST2")
            _assign_warehouse(session, rep.id, dest_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_transfer_with_lines(session, su, source_wh, dest_wh, [product])

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)

            assert transfer.transfer_number in response.text
            assert "INBOUND" in response.text
        finally:
            session.close()


# =======================================================================
# 6. Out-of-scope transfer hidden
# =======================================================================

@requires_database
class TestTransferDetailScope:
    def test_out_of_scope_transfer_not_found(self):
        """Transfer exists but rep's warehouse is not involved."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-scope-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            my_wh = _create_warehouse(session, su, "WH-MINE")
            other_wh = _create_warehouse(session, su, "WH-OTHER")
            other_wh2 = _create_warehouse(session, su, "WH-OTHER2")
            _assign_warehouse(session, rep.id, my_wh.id, su.id)

            product = _create_product(session, su)
            # Transfer between two unrelated warehouses.
            transfer = _create_transfer_with_lines(session, su, other_wh, other_wh2, [product])

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)

            # Must say "not found" — must NOT reveal transfer details.
            assert "not found" in response.text.lower()
            # Must not show warehouse codes or status (would reveal it exists)
            assert other_wh.code not in response.text
            assert other_wh2.code not in response.text
        finally:
            session.close()


# =======================================================================
# 7. Cross-representative isolation
# =======================================================================

@requires_database
class TestTransferDetailIsolation:
    def test_rep_cannot_see_other_reps_transfer(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A
            puid_a = f"trd-a-{uuid.uuid4().hex[:6]}"
            rep_a, user_a, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            wh_a = _create_warehouse(session, su, "WH-A")
            _assign_warehouse(session, rep_a.id, wh_a.id, su.id)

            # Rep B
            puid_b = f"trd-b-{uuid.uuid4().hex[:6]}"
            rep_b, user_b, _ = _make_bound_session(session, su, platform_user_id=puid_b)
            wh_b = _create_warehouse(session, su, "WH-B")
            _assign_warehouse(session, rep_b.id, wh_b.id, su.id)

            product = _create_product(session, su)
            # Transfer A->B: visible to both
            t_ab = _create_transfer_with_lines(session, su, wh_a, wh_b, [product])
            # Transfer B->some_other: visible only to B
            wh_c = _create_warehouse(session, su, "WH-C")
            t_bc = _create_transfer_with_lines(session, su, wh_b, wh_c, [product])

            # Rep A can see A->B
            msg_a1 = BotMessage(platform_user_id=puid_a, platform_code="TELEGRAM", text=f"/transfer {t_ab.transfer_number}")
            resp_a1 = process_message(session, message=msg_a1)
            assert t_ab.transfer_number in resp_a1.text

            # Rep A cannot see B->C
            msg_a2 = BotMessage(platform_user_id=puid_a, platform_code="TELEGRAM", text=f"/transfer {t_bc.transfer_number}")
            resp_a2 = process_message(session, message=msg_a2)
            assert "not found" in resp_a2.text.lower()

            # Rep B can see both
            msg_b1 = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/transfer {t_ab.transfer_number}")
            resp_b1 = process_message(session, message=msg_b1)
            assert t_ab.transfer_number in resp_b1.text

            msg_b2 = BotMessage(platform_user_id=puid_b, platform_code="TELEGRAM", text=f"/transfer {t_bc.transfer_number}")
            resp_b2 = process_message(session, message=msg_b2)
            assert t_bc.transfer_number in resp_b2.text
        finally:
            session.close()


# =======================================================================
# 8. Detail data correctness
# =======================================================================

@requires_database
class TestTransferDetailData:
    def test_multiple_lines_shown(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-ml-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            source_wh = _create_warehouse(session, su, "WH-SRC3")
            dest_wh = _create_warehouse(session, su, "WH-DST3")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product1 = _create_product(session, su)
            product2 = _create_product(session, su)
            transfer = _create_transfer_with_lines(session, su, source_wh, dest_wh, [product1, product2])

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)

            assert product1.sku in response.text
            assert product2.sku in response.text
            assert "Items:" in response.text
        finally:
            session.close()

    def test_no_uuid_leakage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-uuid-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            source_wh = _create_warehouse(session, su, "WH-SRC4")
            dest_wh = _create_warehouse(session, su, "WH-DST4")
            _assign_warehouse(session, rep.id, source_wh.id, su.id)

            product = _create_product(session, su)
            transfer = _create_transfer_with_lines(session, su, source_wh, dest_wh, [product])

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text=f"/transfer {transfer.transfer_number}")
            response = process_message(session, message=msg)

            assert str(transfer.id) not in response.text
            assert str(rep.id) not in response.text
            assert str(product.id) not in response.text
        finally:
            session.close()


# =======================================================================
# 9. Regression
# =======================================================================

@requires_database
class TestTransferDetailRegression:
    def test_transfers_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-regtr-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/transfers")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()

    def test_orders_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"trd-regord-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)
            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/orders")
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
        finally:
            session.close()
