"""Tests proving GET /api/v1/commission-transactions representative scope
is enforced (M-16 security fix).

Covers:
1. Representative sees only own transactions when representative_id omitted.
2. Representative gets 403 when explicitly querying another rep's transactions.
3. Representative supplying their own representative_id still works.
4. Admin/staff user retains unscoped list behavior.
5. Admin can optionally filter by representative_id.

All tests use real PostgreSQL (same skipif convention as other test files).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.commission_config import CommissionConfig
from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.representative import Representative
from database.session import get_session_factory
from services import (
    auth_service,
    bootstrap_service,
    commission_service,
    inventory_service,
    order_service,
    rbac_service,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping commission transaction list scope tests",
)

COMMISSION_MANAGE = "COMMISSION_MANAGE"
ORDER_MANAGE = "ORDER_MANAGE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_rep_user(session, system_user, rep, *, suffix: str) -> dict:
    """Create a user linked to a representative, grant permissions, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"txnscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session,
        username=username,
        email=f"{username}@example.invalid",
        password=password,
        created_by=system_user.id,
    )
    # Link user to representative
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_TXNSCOPE_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"TxnScope {suffix}", created_by=system_user.id,
    )
    for code in (COMMISSION_MANAGE, ORDER_MANAGE):
        try:
            rbac_service.create_permission(
                session, code=code, name=code, resource="commission", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
    rbac_service.assign_role(
        session, user_id=user.id, role_code=role_code, assigned_by=system_user.id,
    )
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str) -> dict:
    """Create an admin user (no representative link), grant permissions, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"txnscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session,
        username=username,
        email=f"{username}@example.invalid",
        password=password,
        created_by=system_user.id,
    )
    # No representative_id set -- admin/staff user
    session.flush()

    role_code = f"ROLE_TXNSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"TxnScopeAdmin {suffix}", created_by=system_user.id,
    )
    for code in (COMMISSION_MANAGE, ORDER_MANAGE):
        try:
            rbac_service.create_permission(
                session, code=code, name=code, resource="commission", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
    rbac_service.assign_role(
        session, user_id=user.id, role_code=role_code, assigned_by=system_user.id,
    )
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _setup(client: TestClient):
    """Create two representatives, commission transactions for each, return context."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        # Two representatives
        rep_a = Representative(
            code=f"REPA-TLS-{suffix}", person_name="Rep A TxnScope", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-TLS-{suffix}", person_name="Rep B TxnScope", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        # Commission configs for each rep (10% rate)
        now = datetime.datetime.now(datetime.timezone.utc)
        config_a = CommissionConfig(
            representative_id=rep_a.id, order_type="LOCAL", rate=decimal.Decimal("10.00"),
            effective_from=now - datetime.timedelta(days=30), created_by=system_user.id,
            updated_by=system_user.id,
        )
        config_b = CommissionConfig(
            representative_id=rep_b.id, order_type="LOCAL", rate=decimal.Decimal("10.00"),
            effective_from=now - datetime.timedelta(days=30), created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add_all([config_a, config_b])
        session.flush()

        # Create commission transactions directly (no orders needed for list scope)
        txn_a = CommissionTransaction(
            representative_id=rep_a.id, order_id=None,
            commission_config_id=config_a.id, actor_user_id=system_user.id,
            sequence_no=1, signed_amount=decimal.Decimal("100.00"),
            state_event="ACCRUED", rate_applied=decimal.Decimal("10.00"),
            currency_id=currency.id,
        )
        txn_b = CommissionTransaction(
            representative_id=rep_b.id, order_id=None,
            commission_config_id=config_b.id, actor_user_id=system_user.id,
            sequence_no=1, signed_amount=decimal.Decimal("200.00"),
            state_event="ACCRUED", rate_applied=decimal.Decimal("10.00"),
            currency_id=currency.id,
        )
        session.add_all([txn_a, txn_b])
        session.flush()

        # Two users: one per representative
        user_a_headers, _ = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        user_b_headers, _ = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        admin_headers, _ = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

    return {
        "headers_a": user_a_headers,
        "headers_b": user_b_headers,
        "headers_admin": admin_headers,
        "txn_a_id": str(txn_a.id),
        "txn_b_id": str(txn_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_database
class TestCommissionTransactionListScope:
    """GET /commission-transactions representative scope enforcement (M-16)."""

    def test_representative_sees_only_own_transactions(self, client: TestClient):
        """When representative_id is omitted, rep sees only their own transactions."""
        data = _setup(client)
        resp = client.get("/api/v1/commission-transactions", headers=data["headers_a"])
        assert resp.status_code == 200
        items = resp.json()["items"]
        txn_ids = [t["id"] for t in items]
        assert data["txn_a_id"] in txn_ids
        assert data["txn_b_id"] not in txn_ids

    def test_representative_gets_403_for_other_rep(self, client: TestClient):
        """Explicitly querying another representative's transactions returns 403."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/commission-transactions?representative_id={data['rep_b_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 403
        assert "different representative" in resp.json()["detail"].lower()

    def test_representative_own_representative_id_works(self, client: TestClient):
        """Supplying own representative_id still works (returns own transactions)."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/commission-transactions?representative_id={data['rep_a_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        txn_ids = [t["id"] for t in items]
        assert data["txn_a_id"] in txn_ids
        assert data["txn_b_id"] not in txn_ids

    def test_admin_sees_all_transactions(self, client: TestClient):
        """Admin/staff user (no representative) sees all transactions."""
        data = _setup(client)
        resp = client.get("/api/v1/commission-transactions", headers=data["headers_admin"])
        assert resp.status_code == 200
        items = resp.json()["items"]
        txn_ids = [t["id"] for t in items]
        assert data["txn_a_id"] in txn_ids
        assert data["txn_b_id"] in txn_ids

    def test_admin_can_filter_by_representative(self, client: TestClient):
        """Admin can optionally filter by representative_id."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/commission-transactions?representative_id={data['rep_a_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        txn_ids = [t["id"] for t in items]
        assert data["txn_a_id"] in txn_ids
        assert data["txn_b_id"] not in txn_ids

    def test_unauthenticated_returns_401(self, client: TestClient):
        """Unauthenticated requests are rejected."""
        resp = client.get("/api/v1/commission-transactions")
        assert resp.status_code in (401, 403)
