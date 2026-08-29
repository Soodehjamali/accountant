"""Tests proving customer ledger endpoint representative scope enforcement (F-06).

Covers:
1. Representative can read own/in-scope customer ledger.
2. Representative cannot read another representative's customer ledger → 404.
3. Nonexistent customer → 404.
4. Admin can read any customer ledger.
5. Representative can read own/in-scope customer balance.
6. Representative cannot read another representative's customer balance → 404.
7. Admin can read any customer balance.
8. Representative can reconcile own customer when business state permits.
9. Representative cannot reconcile another representative's customer → 404.
10. Out-of-scope reconcile causes zero financial side effects.
11. Admin can reconcile any customer when business state permits.

All tests use real PostgreSQL.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from sqlalchemy import select

from database.models.customer import Customer
from database.models.customer_ledger import CustomerLedger
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, customer_ledger_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping customer ledger scope tests",
)

CUSTOMER_LEDGER_VIEW = "CUSTOMER_LEDGER_VIEW"
CUSTOMER_LEDGER_MANAGE = "CUSTOMER_LEDGER_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant ledger permissions, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"clscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_CLSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CLScope {suffix}", created_by=system_user.id)
    for code in [CUSTOMER_LEDGER_VIEW, CUSTOMER_LEDGER_MANAGE]:
        try:
            rbac_service.create_permission(
                session, code=code, name=code, resource="customer_ledger", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant ledger permissions, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"clscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_CLSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CLScopeAdmin {suffix}", created_by=system_user.id)
    for code in [CUSTOMER_LEDGER_VIEW, CUSTOMER_LEDGER_MANAGE]:
        try:
            rbac_service.create_permission(
                session, code=code, name=code, resource="customer_ledger", action="manage",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _setup(client):
    """Create two reps, two customers (each assigned to a rep), ledger headers, and seed entries.

    Rep A → Customer A (active assignment)
    Rep B → Customer B (active assignment)
    Customer A has a ledger with 1 invoice entry (+100).
    """
    from datetime import timedelta

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        # Create two representatives
        rep_a = Representative(
            code=f"REPA-CLS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-CLS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        # Create customers
        cust_a = Customer(
            code=f"CUSTA-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        cust_b = Customer(
            code=f"CUSTB-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([cust_a, cust_b])
        session.flush()

        # Assign customers to representatives
        now = _now()
        assign_a = CustomerRepAssignment(
            customer_id=cust_a.id, representative_id=rep_a.id,
            effective_from=now, effective_to=now + timedelta(days=365),
            priority=1,
            created_by=system_user.id, updated_by=system_user.id,
        )
        assign_b = CustomerRepAssignment(
            customer_id=cust_b.id, representative_id=rep_b.id,
            effective_from=now, effective_to=now + timedelta(days=365),
            priority=1,
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([assign_a, assign_b])
        session.flush()

        # Create ledger headers
        ledger_a = customer_ledger_service.ensure_customer_ledger(
            session, customer_id=cust_a.id, currency_id=currency.id,
        )
        ledger_b = customer_ledger_service.ensure_customer_ledger(
            session, customer_id=cust_b.id, currency_id=currency.id,
        )
        session.flush()

        # Seed a ledger entry for customer A
        customer_ledger_service.record_entry(
            session,
            customer_id=cust_a.id,
            reference_type="invoice",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("100.0000"),
            currency_id=currency.id,
            entry_type="INVOICE_ISSUED",
            actor_user_id=system_user.id,
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
        "cust_a_id": str(cust_a.id),
        "cust_b_id": str(cust_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestCustomerLedgerReadScope:
    """GET /customers/{customer_id}/ledger representative scope enforcement."""

    def test_representative_can_read_own_customer_ledger(self, client):
        """Representative can read ledger for their own assigned customer."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_a_id']}/ledger",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 1  # seeded entry

    def test_representative_cannot_read_other_rep_customer_ledger(self, client):
        """Representative cannot read ledger for another rep's customer → 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_b_id']}/ledger",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_nonexistent_customer_returns_404(self, client):
        """Nonexistent customer returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/customers/{fake_id}/ledger",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_customer_ledger(self, client):
        """Admin/staff can read any customer ledger."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_a_id']}/ledger",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text


@requires_database
class TestCustomerBalanceScope:
    """GET /customers/{customer_id}/balance representative scope enforcement."""

    def test_representative_can_read_own_customer_balance(self, client):
        """Representative can read balance for their own assigned customer."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_a_id']}/balance",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert decimal.Decimal(resp.json()["balance"]) == decimal.Decimal("100.0000")

    def test_representative_cannot_read_other_rep_customer_balance(self, client):
        """Representative cannot read balance for another rep's customer → 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_b_id']}/balance",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_customer_balance(self, client):
        """Admin/staff can read any customer balance."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_a_id']}/balance",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text


@requires_database
class TestCustomerLedgerReconcileScope:
    """POST /customers/{customer_id}/ledger/reconcile representative scope enforcement."""

    def test_representative_can_reconcile_own_customer(self, client):
        """Representative can reconcile their own customer's ledger."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/customers/{data['cust_a_id']}/ledger/reconcile",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text

    def test_representative_cannot_reconcile_other_rep_customer(self, client):
        """Representative cannot reconcile another rep's customer → 404."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/customers/{data['cust_b_id']}/ledger/reconcile",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_reconcile_produces_no_side_effects(self, client):
        """Out-of-scope reconcile changes nothing — ledger state unchanged."""
        data = _setup(client)

        # Capture before state
        session = get_session_factory()()
        try:
            ledger = session.execute(
                select(CustomerLedger).where(
                    CustomerLedger.customer_id == uuid.UUID(data["cust_b_id"]),
                )
            ).scalar_one_or_none()
            balance_before = ledger.current_balance if ledger else None
            reconciled_before = ledger.last_reconciled_at if ledger else None
        finally:
            session.close()

        # Attempt out-of-scope reconcile
        resp = client.post(
            f"/api/v1/customers/{data['cust_b_id']}/ledger/reconcile",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no changes
        session = get_session_factory()()
        try:
            ledger = session.execute(
                select(CustomerLedger).where(
                    CustomerLedger.customer_id == uuid.UUID(data["cust_b_id"]),
                )
            ).scalar_one_or_none()
            assert ledger.current_balance == balance_before, "Balance must not change"
            assert ledger.last_reconciled_at == reconciled_before, "Reconciled timestamp must not change"
        finally:
            session.close()

    def test_admin_can_reconcile_any_customer(self, client):
        """Admin/staff can reconcile any customer."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/customers/{data['cust_a_id']}/ledger/reconcile",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
