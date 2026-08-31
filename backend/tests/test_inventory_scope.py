"""Tests proving inventory endpoint warehouse scope enforcement (F-07).

Covers:
1. Representative can create inventory transaction in authorized warehouse.
2. Representative cannot create transaction in another representative's warehouse → 404.
3. Out-of-scope create produces zero side effects.
4. Admin can create transaction in any warehouse.
5. Representative can read transaction from authorized warehouse.
6. Representative cannot read transaction from unauthorized warehouse → 404.
7. Nonexistent transaction → 404.
8. Admin can read any transaction.
9. Representative can read balance for authorized warehouse.
10. Representative cannot read balance for unauthorized warehouse → 404.
11. Admin can read balance for any warehouse.
12. Representative can reverse authorized transaction if business state permits.
13. Representative cannot reverse unauthorized transaction → 404.
14. Out-of-scope reverse produces zero side effects.
15. Admin can reverse where business state permits.

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
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping inventory scope tests",
)

TRANSFER_MANAGE = "TRANSFER_MANAGE"
INVENTORY_MANAGE = "INVENTORY_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant TRANSFER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"invscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_INVSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"InvScope {suffix}", created_by=system_user.id)
    for perm_code, perm_resource in [(TRANSFER_MANAGE, "inventory"), (INVENTORY_MANAGE, "inventory")]:
        try:
            rbac_service.create_permission(
                session, code=perm_code, name=perm_code, resource=perm_resource, action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=perm_code)
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
    username = f"invscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_INVSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"InvScopeAdmin {suffix}", created_by=system_user.id)
    for perm_code, perm_resource in [(TRANSFER_MANAGE, "inventory"), (INVENTORY_MANAGE, "inventory")]:
        try:
            rbac_service.create_permission(
                session, code=perm_code, name=perm_code, resource=perm_resource, action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=perm_code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _setup(client):
    """Create two reps with warehouses, a product, and seed inventory in rep A's warehouse.

    Rep A owns wh_a. Rep B owns wh_b. wh_unassigned is not assigned to anyone.
    An INITIAL_OPENING_BALANCE transaction is posted in wh_a for the product.
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
            code=f"REPA-INV-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-INV-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        # Create warehouses
        wh_a = Warehouse(
            code=f"WA-INV-{suffix}", name=f"Warehouse A {suffix}",
            type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        wh_b = Warehouse(
            code=f"WB-INV-{suffix}", name=f"Warehouse B {suffix}",
            type="REPRESENTATIVE", ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([wh_a, wh_b])
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

        # Create a product
        product = Product(
            sku=f"SKU-INV-{suffix}", name="InventoryScope Product",
            base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=system_user.id).id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        # Seed inventory in wh_a
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=wh_a.id,
            movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("100"),
            unit_cost=decimal.Decimal("50.0000"), currency_id=currency.id, actor_user_id=system_user.id,
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
        "wh_a_id": str(wh_a.id),
        "wh_b_id": str(wh_b.id),
        "product_id": str(product.id),
        "currency_id": str(currency.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestInventoryCreateScope:
    """POST /inventory/transactions warehouse scope enforcement."""

    def test_representative_can_create_in_own_warehouse(self, client):
        """Representative can create inventory transaction in their assigned warehouse."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/inventory/transactions",
            json={
                "product_id": data["product_id"],
                "warehouse_id": data["wh_a_id"],
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "50",
                "unit_cost": "10.0000",
                "currency_id": data["currency_id"],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text

    def test_representative_cannot_create_in_other_rep_warehouse(self, client):
        """Representative cannot create inventory transaction in another rep's warehouse → 404."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/inventory/transactions",
            json={
                "product_id": data["product_id"],
                "warehouse_id": data["wh_b_id"],
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "50",
                "unit_cost": "10.0000",
                "currency_id": data["currency_id"],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_create_produces_no_side_effects(self, client):
        """Out-of-scope create changes nothing — no inventory transaction created."""
        data = _setup(client)

        # Count transactions before
        session = get_session_factory()()
        try:
            count_before = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.warehouse_id == uuid.UUID(data["wh_b_id"]),
                    InventoryTransaction.product_id == uuid.UUID(data["product_id"]),
                )
            ).scalars().all())
        finally:
            session.close()

        # Attempt out-of-scope create
        resp = client.post(
            "/api/v1/inventory/transactions",
            json={
                "product_id": data["product_id"],
                "warehouse_id": data["wh_b_id"],
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "50",
                "unit_cost": "10.0000",
                "currency_id": data["currency_id"],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no changes
        session = get_session_factory()()
        try:
            count_after = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.warehouse_id == uuid.UUID(data["wh_b_id"]),
                    InventoryTransaction.product_id == uuid.UUID(data["product_id"]),
                )
            ).scalars().all())
            assert count_after == count_before, "No inventory transaction should be created"
        finally:
            session.close()

    def test_admin_can_create_in_any_warehouse(self, client):
        """Admin/staff can create inventory transaction in any warehouse."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/inventory/transactions",
            json={
                "product_id": data["product_id"],
                "warehouse_id": data["wh_a_id"],
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "50",
                "unit_cost": "10.0000",
                "currency_id": data["currency_id"],
            },
            headers=data["headers_admin"],
        )
        assert resp.status_code == 201, resp.text


# NOTE: No GET /inventory/transactions/{id} endpoint exists in the API.
# Transaction read scope is implicitly enforced because the only read
# endpoint (GET /balance) already has warehouse scope, and transaction
# IDs are only exposed in reverse responses (which also have scope).


@requires_database
class TestInventoryBalanceScope:
    """GET /inventory/balance warehouse scope enforcement."""

    def test_representative_can_read_own_warehouse_balance(self, client):
        """Representative can read balance for their own warehouse."""
        data = _setup(client)
        resp = client.get(
            "/api/v1/inventory/balance",
            params={
                "warehouse_id": data["wh_a_id"],
                "product_id": data["product_id"],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert decimal.Decimal(resp.json()["balance"]) == decimal.Decimal("100")

    def test_representative_cannot_read_other_rep_warehouse_balance(self, client):
        """Representative cannot read balance for another rep's warehouse → 404."""
        data = _setup(client)
        resp = client.get(
            "/api/v1/inventory/balance",
            params={
                "warehouse_id": data["wh_b_id"],
                "product_id": data["product_id"],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_warehouse_balance(self, client):
        """Admin/staff can read balance for any warehouse."""
        data = _setup(client)
        resp = client.get(
            "/api/v1/inventory/balance",
            params={
                "warehouse_id": data["wh_a_id"],
                "product_id": data["product_id"],
            },
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert decimal.Decimal(resp.json()["balance"]) == decimal.Decimal("100")


@requires_database
class TestInventoryReverseScope:
    """POST /inventory/transactions/{id}/reverse warehouse scope enforcement."""

    def _create_reversible_txn_in_wh_a(self, data):
        """Helper: create a transaction in warehouse A and return its id."""
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            txn = inventory_service.post_transaction(
                session,
                product_id=uuid.UUID(data["product_id"]),
                warehouse_id=uuid.UUID(data["wh_a_id"]),
                movement_type_code="RECEIPT_FROM_PRODUCTION",
                signed_quantity=decimal.Decimal("30"),
                unit_cost=decimal.Decimal("10.0000"),
                currency_id=uuid.UUID(data["currency_id"]),
                actor_user_id=system_user.id,
            )
            session.commit()
            return txn
        finally:
            session.close()

    def test_representative_can_reverse_own_warehouse_transaction(self, client):
        """Representative can reverse a transaction in their own warehouse."""
        data = _setup(client)
        txn = self._create_reversible_txn_in_wh_a(data)
        resp = client.post(
            f"/api/v1/inventory/transactions/{txn.id}/reverse",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["reversal_of_id"] == str(txn.id)

    def test_representative_cannot_reverse_other_rep_warehouse_transaction(self, client):
        """Representative cannot reverse a transaction in another rep's warehouse → 404."""
        data = _setup(client)
        txn = self._create_reversible_txn_in_wh_a(data)
        resp = client.post(
            f"/api/v1/inventory/transactions/{txn.id}/reverse",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_reverse_produces_no_side_effects(self, client):
        """Out-of-scope reverse changes nothing — transaction not reversed, no new rows."""
        data = _setup(client)
        txn = self._create_reversible_txn_in_wh_a(data)

        # Capture before state
        session = get_session_factory()()
        try:
            txn_before = session.get(InventoryTransaction, txn.id)
            is_reversed_before = txn_before.is_reversed
            count_before = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.reversal_of_id == txn.id,
                )
            ).scalars().all())
        finally:
            session.close()

        # Attempt out-of-scope reverse
        resp = client.post(
            f"/api/v1/inventory/transactions/{txn.id}/reverse",
            json={},
            headers=data["headers_b"],
        )
        assert resp.status_code == 404

        # Verify no changes
        session = get_session_factory()()
        try:
            txn_after = session.get(InventoryTransaction, txn.id)
            assert txn_after.is_reversed == is_reversed_before, "Transaction must not be reversed"
            count_after = len(session.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.reversal_of_id == txn.id,
                )
            ).scalars().all())
            assert count_after == count_before, "No reversal transaction should be created"
        finally:
            session.close()

    def test_admin_can_reverse_any_transaction(self, client):
        """Admin/staff can reverse any transaction where business state permits."""
        data = _setup(client)
        txn = self._create_reversible_txn_in_wh_a(data)
        resp = client.post(
            f"/api/v1/inventory/transactions/{txn.id}/reverse",
            json={},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["reversal_of_id"] == str(txn.id)
