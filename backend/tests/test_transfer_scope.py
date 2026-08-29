"""Tests proving stock transfer endpoint warehouse/representative scope enforcement.

Covers:
1. Representative can read own/in-scope transfer.
2. Representative cannot read out-of-scope transfer → 404.
3. Nonexistent transfer → 404.
4. Admin/staff can read any transfer.
5. Representative can dispatch authorized transfer.
6. Representative cannot dispatch out-of-scope transfer → 404.
7. Out-of-scope dispatch produces ZERO side effects.
8. Representative can receive authorized transfer.
9. Representative cannot receive out-of-scope transfer → 404.
10. Out-of-scope receive produces ZERO side effects.
11. Representative can cancel authorized transfer.
12. Representative cannot cancel out-of-scope transfer → 404.
13. Out-of-scope cancel produces ZERO side effects.

Business rule (from bot layer): a transfer is accessible if EITHER
source OR destination warehouse belongs to the representative.

All tests use real PostgreSQL.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from sqlalchemy import select

from database.models.inventory_transaction import InventoryTransaction
from database.models.representative import Representative
from database.models.product import Product
from database.models.stock_transfer import StockTransfer
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping transfer scope tests",
)

TRANSFER_MANAGE = "TRANSFER_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant TRANSFER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"trscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_TRSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"TrScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=TRANSFER_MANAGE, name=TRANSFER_MANAGE, resource="stock_transfer", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=TRANSFER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant TRANSFER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"trscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_TRSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"TrScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=TRANSFER_MANAGE, name=TRANSFER_MANAGE, resource="stock_transfer", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=TRANSFER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _setup(client):
    """Create two reps with warehouse assignments, two warehouses, one transfer.

    Rep A owns warehouse_src_a. Rep B owns warehouse_src_b.
    Transfer goes warehouse_src_a → warehouse_dest (shared/unassigned).
    Rep A can access this transfer (source is theirs).
    Rep B cannot (neither warehouse is theirs).
    """
    from datetime import timedelta

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)
        bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        # Create two representatives
        rep_a = Representative(
            code=f"REPA-TRS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-TRS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        # Create warehouses
        wh_a = Warehouse(
            code=f"WA-{suffix}", name=f"Warehouse A {suffix}",
            type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        wh_b = Warehouse(
            code=f"WB-{suffix}", name=f"Warehouse B {suffix}",
            type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        wh_dest = Warehouse(
            code=f"WD-{suffix}", name=f"Warehouse Dest {suffix}",
            type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([wh_a, wh_b, wh_dest])
        session.flush()

        # Assign warehouses to representatives
        now = _now()
        assign_a = WarehouseAssignment(
            representative_id=rep_a.id, warehouse_id=wh_a.id,
            is_primary=True, effective_from=now, effective_to=now + timedelta(days=365),
            created_by=system_user.id, updated_by=system_user.id,
        )
        assign_b = WarehouseAssignment(
            representative_id=rep_b.id, warehouse_id=wh_b.id,
            is_primary=True, effective_from=now, effective_to=now + timedelta(days=365),
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([assign_a, assign_b])
        session.flush()

        # Create a product with inventory at warehouse A
        product = Product(
            sku=f"SKU-TRS-{suffix}", name="TransferScope Product",
            base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=system_user.id).id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=wh_a.id,
            movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"), currency_id=currency.id, actor_user_id=system_user.id,
        )
        session.flush()

        # Create a DRAFT transfer from wh_a → wh_dest (scope: source = wh_a = rep_a)
        from services import stock_transfer_service
        transfer = stock_transfer_service.create_transfer(
            session,
            source_warehouse_id=wh_a.id,
            destination_warehouse_id=wh_dest.id,
            lines=[stock_transfer_service.TransferLineInput(
                product_id=product.id, qty_requested=decimal.Decimal("100"),
                unit_cost=decimal.Decimal("50.0000"),
            )],
            requested_by=system_user.id,
        )
        session.flush()

        # Create users
        headers_a, user_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        headers_b, user_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

    return {
        "headers_a": headers_a,
        "headers_b": headers_b,
        "headers_admin": headers_admin,
        "transfer_id": str(transfer.id),
        "wh_a_id": str(wh_a.id),
        "wh_b_id": str(wh_b.id),
        "wh_dest_id": str(wh_dest.id),
        "product_id": str(product.id),
        "currency_id": str(currency.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestTransferReadScope:
    """GET /transfers/{transfer_id} warehouse scope enforcement."""

    def test_representative_can_read_own_transfer(self, client):
        """Representative can read a transfer involving their warehouse."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/transfers/{data['transfer_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["transfer_id"]

    def test_representative_cannot_read_out_of_scope_transfer(self, client):
        """Representative cannot read a transfer involving no warehouse they own — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/transfers/{data['transfer_id']}",
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

    def test_nonexistent_transfer_returns_404(self, client):
        """Nonexistent transfer returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/transfers/{fake_id}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_transfer(self, client):
        """Admin/staff user can read any transfer."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/transfers/{data['transfer_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["transfer_id"]


@requires_database
class TestTransferDispatchScope:
    """POST /transfers/{id}/dispatch warehouse scope enforcement."""

    def test_representative_can_dispatch_own_transfer(self, client):
        """Representative can dispatch a transfer involving their warehouse."""
        data = _setup(client)
        # Submit + Approve before dispatch.
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/submit",
            json={}, headers=data["headers_a"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/approve",
            json={}, headers=data["headers_a"],
        )
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/dispatch",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "DISPATCHED"

    def test_representative_cannot_dispatch_out_of_scope(self, client):
        """Representative cannot dispatch a transfer involving no warehouse they own — 404."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/dispatch",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_dispatch_produces_no_side_effects(self, client):
        """Out-of-scope dispatch changes nothing — transfer state, inventory, transactions unchanged."""
        data = _setup(client)

        # Capture before state.
        session = get_session_factory()()
        try:
            transfer = session.get(StockTransfer, uuid.UUID(data["transfer_id"]))
            state_before = transfer.state
            inv_count_before = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.warehouse_id == uuid.UUID(data["wh_a_id"]),
                    InventoryTransaction.product_id == uuid.UUID(data["product_id"]),
                )
            ).scalars().all())
        finally:
            session.close()

        # Attempt out-of-scope dispatch.
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/dispatch",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

        # Verify no changes.
        session = get_session_factory()()
        try:
            transfer = session.get(StockTransfer, uuid.UUID(data["transfer_id"]))
            assert transfer.state == state_before, "Transfer state must not change"
            inv_count_after = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.warehouse_id == uuid.UUID(data["wh_a_id"]),
                    InventoryTransaction.product_id == uuid.UUID(data["product_id"]),
                )
            ).scalars().all())
            assert inv_count_after == inv_count_before, "No inventory transaction should be created"
        finally:
            session.close()


@requires_database
class TestTransferReceiveScope:
    """POST /transfers/{id}/receive warehouse scope enforcement."""

    def test_representative_can_receive_own_transfer(self, client):
        """Representative can receive a dispatched transfer involving their warehouse."""
        data = _setup(client)
        # Submit + Approve + Dispatch first.
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/submit",
            json={}, headers=data["headers_a"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/approve",
            json={}, headers=data["headers_a"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/dispatch",
            json={}, headers=data["headers_a"],
        )
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/receive",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "RECEIVED"

    def test_representative_cannot_receive_out_of_scope(self, client):
        """Representative cannot receive a transfer involving no warehouse they own — 404."""
        data = _setup(client)
        # Submit + Approve + Dispatch as admin (who has access).
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/submit",
            json={}, headers=data["headers_admin"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/approve",
            json={}, headers=data["headers_admin"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/dispatch",
            json={}, headers=data["headers_admin"],
        )
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/receive",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_receive_produces_no_side_effects(self, client):
        """Out-of-scope receive changes nothing."""
        data = _setup(client)
        # Submit + Approve + Dispatch as admin.
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/submit",
            json={}, headers=data["headers_admin"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/approve",
            json={}, headers=data["headers_admin"],
        )
        client.post(
            f"/api/v1/transfers/{data['transfer_id']}/dispatch",
            json={}, headers=data["headers_admin"],
        )

        # Capture before state.
        session = get_session_factory()()
        try:
            transfer = session.get(StockTransfer, uuid.UUID(data["transfer_id"]))
            state_before = transfer.state
            inv_count_before = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.warehouse_id == uuid.UUID(data["wh_dest_id"]),
                    InventoryTransaction.product_id == uuid.UUID(data["product_id"]),
                )
            ).scalars().all())
        finally:
            session.close()

        # Attempt out-of-scope receive.
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/receive",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

        # Verify no changes.
        session = get_session_factory()()
        try:
            transfer = session.get(StockTransfer, uuid.UUID(data["transfer_id"]))
            assert transfer.state == state_before
            inv_count_after = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.warehouse_id == uuid.UUID(data["wh_dest_id"]),
                    InventoryTransaction.product_id == uuid.UUID(data["product_id"]),
                )
            ).scalars().all())
            assert inv_count_after == inv_count_before
        finally:
            session.close()


@requires_database
class TestTransferCancelScope:
    """POST /transfers/{id}/cancel warehouse scope enforcement."""

    def test_representative_can_cancel_own_transfer(self, client):
        """Representative can cancel a DRAFT transfer involving their warehouse."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/cancel",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "CANCELLED"

    def test_representative_cannot_cancel_out_of_scope(self, client):
        """Representative cannot cancel a transfer involving no warehouse they own — 404."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/cancel",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_cancel_produces_no_side_effects(self, client):
        """Out-of-scope cancel changes nothing — transfer state unchanged."""
        data = _setup(client)

        session = get_session_factory()()
        try:
            transfer = session.get(StockTransfer, uuid.UUID(data["transfer_id"]))
            state_before = transfer.state
        finally:
            session.close()

        resp = client.post(
            f"/api/v1/transfers/{data['transfer_id']}/cancel",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

        session = get_session_factory()()
        try:
            transfer = session.get(StockTransfer, uuid.UUID(data["transfer_id"]))
            assert transfer.state == state_before, "Transfer state must not change"
        finally:
            session.close()
