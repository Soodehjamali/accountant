"""Focused tests for the /create-transfer bot command (Tier 2 — direct write).

Covers:
- BOT_WRITE required
- Missing arguments
- Nonexistent warehouse
- Nonexistent product
- Same source/destination warehouse
- Invalid quantity / unit cost
- DRAFT transfer created successfully
- Warehouse scope: source not assigned
- Warehouse scope: destination not assigned
- Cross-representative isolation
- Audit/history recorded

All tests use the real PostgreSQL database.
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
from services import auth_service, bootstrap_service, rbac_service
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
    reason="DATABASE_URL not set; skipping /create-transfer tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    rep = Representative(
        code=f"REP-CTF-{suffix.upper()}",
        person_name=f"CreateTransfer Rep {suffix}",
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session, su, rep):
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(
        session,
        username=f"ctf_user_{suffix}",
        email=f"ctf_{suffix}@test.invalid",
        password="test-password-123",
        created_by=su.id,
        representative_id=rep.id,
    )


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


def _make_bound_session(session, su, *, platform_user_id):
    rep = _create_representative(session, su)
    user = _create_app_user(session, su, rep)
    _grant_bot_query(session, user, su)
    _grant_bot_write(session, user, su)
    _ensure_telegram_platform(session)
    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM", created_by=su.id
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=user.id,
    )
    return rep, user, bot_session


def _make_bound_session_no_write(session, su, *, platform_user_id):
    rep = _create_representative(session, su)
    user = _create_app_user(session, su, rep)
    _grant_bot_query(session, user, su)
    _ensure_telegram_platform(session)
    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM", created_by=su.id
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=user.id,
    )
    return rep, user, bot_session


def _create_warehouse(session, su, prefix="WH-CTF"):
    suffix = uuid.uuid4().hex[:6]
    wh = Warehouse(
        code=f"{prefix}-{suffix}",
        name=f"CreateTransfer WH {suffix}",
        type="REPRESENTATIVE",
        ownership_mode="OWNED",
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(wh)
    session.flush()
    return wh


def _create_product(session, su):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-CTF-{suffix}",
        name=f"CreateTransfer Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=su.id).id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(product)
    session.flush()
    return product


def _assign_warehouse(session, rep_id, wh_id, su_id, *, is_primary=True):
    from datetime import datetime, timezone, timedelta
    session.add(WarehouseAssignment(
        representative_id=rep_id,
        warehouse_id=wh_id,
        is_primary=is_primary,
        effective_from=datetime.now(timezone.utc) - timedelta(days=30),
        created_by=su_id,
        updated_by=su_id,
    ))
    session.flush()


# ===========================================================================
# /create-transfer command
# ===========================================================================


@requires_database
class TestCreateTransferSuccess:
    """Successful creation of a DRAFT stock transfer."""

    def test_creates_draft_transfer(self):
        """A valid command should create a DRAFT transfer."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-SRC")
            dst_wh = _create_warehouse(session, su, "WH-CTF-DST")
            product = _create_product(session, su)
            _assign_warehouse(session, rep.id, src_wh.id, su.id)
            _assign_warehouse(session, rep.id, dst_wh.id, su.id, is_primary=False)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} {product.sku} 10",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "TRF-" in response.text
            assert "DRAFT" in response.text
            assert "created" in response.text.lower()
        finally:
            session.close()

    def test_creates_transfer_with_unit_cost(self):
        """Unit cost should be accepted and passed through."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-UC")
            dst_wh = _create_warehouse(session, su, "WH-CTF-UD")
            product = _create_product(session, su)
            _assign_warehouse(session, rep.id, src_wh.id, su.id)
            _assign_warehouse(session, rep.id, dst_wh.id, su.id, is_primary=False)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} {product.sku} 5 25000",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "created" in response.text.lower()
        finally:
            session.close()

    def test_creates_transfer_with_decimal_qty(self):
        """Decimal quantities should be accepted."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-DQ")
            dst_wh = _create_warehouse(session, su, "WH-CTF-DQ2")
            product = _create_product(session, su)
            _assign_warehouse(session, rep.id, src_wh.id, su.id)
            _assign_warehouse(session, rep.id, dst_wh.id, su.id, is_primary=False)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} {product.sku} 2.5",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "created" in response.text.lower()
        finally:
            session.close()


@requires_database
class TestCreateTransferValidation:
    """Validation and error cases."""

    def test_missing_arguments(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-transfer")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_too_few_arguments(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(platform_user_id=puid, platform_code="TELEGRAM", text="/create-transfer WH-A WH-B")
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_nonexistent_source_warehouse(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-transfer WH-NONEXIST WH-B SKU-001 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_nonexistent_destination_warehouse(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-ND")
            _assign_warehouse(session, rep.id, src_wh.id, su.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} WH-NONEXIST SKU-001 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_nonexistent_product(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-NP")
            dst_wh = _create_warehouse(session, su, "WH-CTF-NP2")
            _assign_warehouse(session, rep.id, src_wh.id, su.id)
            _assign_warehouse(session, rep.id, dst_wh.id, su.id, is_primary=False)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} SKU-NONEXIST 10",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_same_warehouse_error(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-SW")
            product = _create_product(session, su)
            _assign_warehouse(session, rep.id, src_wh.id, su.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {src_wh.code} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "different" in response.text.lower()
        finally:
            session.close()

    def test_invalid_quantity_not_a_number(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-transfer WH-A WH-B SKU-001 abc",
            )
            response = process_message(session, message=msg)
            assert "Invalid quantity" in response.text
        finally:
            session.close()

    def test_invalid_quantity_zero(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-transfer WH-A WH-B SKU-001 0",
            )
            response = process_message(session, message=msg)
            assert "positive" in response.text.lower()
        finally:
            session.close()

    def test_invalid_quantity_negative(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-transfer WH-A WH-B SKU-001 -5",
            )
            response = process_message(session, message=msg)
            assert "positive" in response.text.lower()
        finally:
            session.close()

    def test_negative_unit_cost(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-transfer WH-A WH-B SKU-001 10 -5",
            )
            response = process_message(session, message=msg)
            assert "non-negative" in response.text.lower()
        finally:
            session.close()


@requires_database
class TestCreateTransferPermission:
    """Permission enforcement."""

    def test_requires_bot_write(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/create-transfer WH-A WH-B SKU-001 10",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
        finally:
            session.close()


@requires_database
class TestCreateTransferScope:
    """Warehouse scope enforcement."""

    def test_source_not_assigned(self):
        """Rep cannot create transfer from a warehouse they don't own."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-NS")
            dst_wh = _create_warehouse(session, su, "WH-CTF-NS2")
            product = _create_product(session, su)
            # Only assign destination, not source.
            _assign_warehouse(session, rep.id, dst_wh.id, su.id, is_primary=False)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "not assigned" in response.text.lower()
        finally:
            session.close()

    def test_destination_not_assigned(self):
        """Rep cannot create transfer to a warehouse they don't own."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-ND")
            dst_wh = _create_warehouse(session, su, "WH-CTF-ND2")
            product = _create_product(session, su)
            # Only assign source, not destination.
            _assign_warehouse(session, rep.id, src_wh.id, su.id)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} {product.sku} 10",
            )
            response = process_message(session, message=msg)
            assert "not assigned" in response.text.lower()
        finally:
            session.close()

    def test_cross_rep_isolation(self):
        """Rep B must not be able to use Rep A's warehouses."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)

            # Rep A with warehouses
            puid_a = f"ctfa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(session, su, platform_user_id=puid_a)
            src_wh_a = _create_warehouse(session, su, "WH-CTF-XA")
            dst_wh_a = _create_warehouse(session, su, "WH-CTF-XA2")
            _assign_warehouse(session, rep_a.id, src_wh_a.id, su.id)
            _assign_warehouse(session, rep_a.id, dst_wh_a.id, su.id, is_primary=False)
            product = _create_product(session, su)

            # Rep B with no warehouses
            puid_b = f"ctfb-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(session, su, platform_user_id=puid_b)

            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh_a.code} {dst_wh_a.code} {product.sku} 10",
            )
            response_b = process_message(session, message=msg_b)
            assert "not assigned" in response_b.text.lower() or "not found" in response_b.text.lower()
        finally:
            session.close()


@requires_database
class TestCreateTransferAudit:
    """Audit trail verification."""

    def test_transfer_history_recorded(self):
        """Creating a transfer should write a transfer_history row."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_movement_types(session, actor_id=su.id)
            puid = f"ctf-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, su, platform_user_id=puid)

            src_wh = _create_warehouse(session, su, "WH-CTF-AU")
            dst_wh = _create_warehouse(session, su, "WH-CTF-AU2")
            product = _create_product(session, su)
            _assign_warehouse(session, rep.id, src_wh.id, su.id)
            _assign_warehouse(session, rep.id, dst_wh.id, su.id, is_primary=False)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/create-transfer {src_wh.code} {dst_wh.code} {product.sku} 10",
            )
            process_message(session, message=msg)

            # Find the most recent DRAFT transfer (limit 1 to avoid
            # ambiguity with prior test data).
            transfer = session.execute(
                select(StockTransfer).where(StockTransfer.state == "DRAFT").order_by(StockTransfer.requested_at.desc()).limit(1)
            ).scalars().first()
            assert transfer is not None

            # Verify history row exists.
            history = session.execute(
                select(TransferHistory).where(TransferHistory.stock_transfer_id == transfer.id)
            ).scalars().all()
            assert len(history) >= 1
            assert history[0].from_state == "DRAFT"
            assert history[0].to_state == "DRAFT"
        finally:
            session.close()
