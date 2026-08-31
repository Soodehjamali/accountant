"""Regression tests for SECURITY_AUDIT_2026-08-29.md M-03:

POST /inventory/transactions and POST /inventory/transactions/{id}/reverse
must require the INVENTORY_MANAGE permission.  A user with only warehouse
scope but no INVENTORY_MANAGE must get 403.

GET /inventory/balance remains open to any authenticated caller with
warehouse scope (unchanged).

All tests use real PostgreSQL.
"""

from __future__ import annotations

import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping inventory permission tests",
)

INVENTORY_MANAGE = "INVENTORY_MANAGE"


def _seed_ledger_context():
    """Seed system user, currency, warehouse, product, and movement types.
    Returns a dict of UUIDs needed for API calls."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)
        bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)

        from services import product_service

        sku = f"INV-PERM-{uuid.uuid4().hex[:8]}"
        product = product_service.create_product(
            session,
            sku=sku,
            name="Inventory Permission Test Widget",
            base_uom_id=bootstrap_service.ensure_default_uom(session, actor_id=system_user.id).id,
            created_by=system_user.id,
        )
        session.commit()
        return {
            "system_user_id": system_user.id,
            "currency_id": currency.id,
            "warehouse_id": warehouse.id,
            "product_id": product.id,
        }
    finally:
        session.close()


def _create_user_with_permission(session, system_user, *, suffix: str, grant_manage: bool):
    """Create a user, optionally granting INVENTORY_MANAGE.  Returns auth headers dict."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"invperm_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    if grant_manage:
        role_code = f"ROLE_INVPERM_{suffix}"
        rbac_service = __import__("services.rbac_service", fromlist=["rbac_service"])
        rbac_service.create_role(session, code=role_code, name=f"InvPerm {suffix}", created_by=system_user.id)
        try:
            rbac_service.create_permission(
                session, code=INVENTORY_MANAGE, name=INVENTORY_MANAGE, resource="inventory", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=INVENTORY_MANAGE)
        rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
        session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


@requires_database
class TestInventoryMutationPermissionGate:
    """POST /inventory/transactions and POST /reverse require INVENTORY_MANAGE (M-03)."""

    def test_user_without_inventory_manage_gets_403_on_post_transaction(self, client: TestClient):
        """A user with no INVENTORY_MANAGE permission gets 403 on POST /inventory/transactions."""
        ctx = _seed_ledger_context()
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            suffix = uuid.uuid4().hex[:8]
            headers = _create_user_with_permission(session, system_user, suffix=f"no_{suffix}", grant_manage=False)
            session.commit()
        finally:
            session.close()

        resp = client.post(
            "/api/v1/inventory/transactions",
            json={
                "product_id": str(ctx["product_id"]),
                "warehouse_id": str(ctx["warehouse_id"]),
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "10",
                "unit_cost": "5.0000",
                "currency_id": str(ctx["currency_id"]),
            },
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    def test_user_with_inventory_manage_can_post_transaction(self, client: TestClient):
        """A user with INVENTORY_MANAGE can POST /inventory/transactions (201)."""
        ctx = _seed_ledger_context()
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            suffix = uuid.uuid4().hex[:8]
            headers = _create_user_with_permission(session, system_user, suffix=f"yes_{suffix}", grant_manage=True)
            session.commit()
        finally:
            session.close()

        resp = client.post(
            "/api/v1/inventory/transactions",
            json={
                "product_id": str(ctx["product_id"]),
                "warehouse_id": str(ctx["warehouse_id"]),
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "10",
                "unit_cost": "5.0000",
                "currency_id": str(ctx["currency_id"]),
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    def test_user_without_inventory_manage_gets_403_on_reverse(self, client: TestClient):
        """A user with no INVENTORY_MANAGE permission gets 403 on POST /reverse."""
        ctx = _seed_ledger_context()

        # Seed a reversible transaction
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            txn = inventory_service.post_transaction(
                session,
                product_id=ctx["product_id"],
                warehouse_id=ctx["warehouse_id"],
                movement_type_code="RECEIPT_FROM_PRODUCTION",
                signed_quantity=decimal.Decimal("50"),
                unit_cost=decimal.Decimal("5.0000"),
                currency_id=ctx["currency_id"],
                actor_user_id=system_user.id,
            )
            suffix = uuid.uuid4().hex[:8]
            headers = _create_user_with_permission(session, system_user, suffix=f"no_rev_{suffix}", grant_manage=False)
            session.commit()
        finally:
            session.close()

        resp = client.post(
            f"/api/v1/inventory/transactions/{txn.id}/reverse",
            json={},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    def test_user_with_inventory_manage_can_reverse(self, client: TestClient):
        """A user with INVENTORY_MANAGE can POST /reverse (201)."""
        ctx = _seed_ledger_context()

        # Seed a reversible transaction
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            txn = inventory_service.post_transaction(
                session,
                product_id=ctx["product_id"],
                warehouse_id=ctx["warehouse_id"],
                movement_type_code="RECEIPT_FROM_PRODUCTION",
                signed_quantity=decimal.Decimal("50"),
                unit_cost=decimal.Decimal("5.0000"),
                currency_id=ctx["currency_id"],
                actor_user_id=system_user.id,
            )
            suffix = uuid.uuid4().hex[:8]
            headers = _create_user_with_permission(session, system_user, suffix=f"yes_rev_{suffix}", grant_manage=True)
            session.commit()
        finally:
            session.close()

        resp = client.post(
            f"/api/v1/inventory/transactions/{txn.id}/reverse",
            json={},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    def test_get_balance_requires_no_inventory_manage(self, client: TestClient):
        """GET /inventory/balance does NOT require INVENTORY_MANAGE (unchanged)."""
        ctx = _seed_ledger_context()

        # Seed some inventory
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            inventory_service.post_transaction(
                session,
                product_id=ctx["product_id"],
                warehouse_id=ctx["warehouse_id"],
                movement_type_code="INITIAL_OPENING_BALANCE",
                signed_quantity=decimal.Decimal("100"),
                unit_cost=decimal.Decimal("5.0000"),
                currency_id=ctx["currency_id"],
                actor_user_id=system_user.id,
            )
            session.commit()

            # Create user WITHOUT INVENTORY_MANAGE
            suffix = uuid.uuid4().hex[:8]
            headers = _create_user_with_permission(session, system_user, suffix=f"bal_{suffix}", grant_manage=False)
            session.commit()
        finally:
            session.close()

        resp = client.get(
            "/api/v1/inventory/balance",
            params={
                "warehouse_id": str(ctx["warehouse_id"]),
                "product_id": str(ctx["product_id"]),
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert decimal.Decimal(resp.json()["balance"]) == decimal.Decimal("100")
