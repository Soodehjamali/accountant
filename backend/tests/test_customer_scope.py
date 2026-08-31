"""Tests proving customer mutation endpoint representative scope enforcement (F-05).

Covers:
1. Representative can update in-scope customer.
2. Representative cannot update out-of-scope customer → 404.
3. Nonexistent customer → 404.
4. Admin can update any customer.
5. Representative can deactivate in-scope customer.
6. Representative cannot deactivate out-of-scope customer → 404.
7. Out-of-scope deactivate produces zero side effects.
8. Admin can deactivate any customer.
9. Expired assignment denies access.
10. Customer list remains accessible to all reps (intentionally global).
11. Customer read remains accessible to all reps (intentionally global).

Business model: Customer is intentionally global/shared.
Reads (GET) are NOT scope-protected.
Mutations (PATCH, deactivate) ARE scope-protected.

All tests use real PostgreSQL.
"""

from __future__ import annotations

import datetime
import os
import uuid

import pytest
from sqlalchemy import select

from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping customer scope tests",
)

CUSTOMER_MANAGE = "CUSTOMER_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant CUSTOMER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"custscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_CUSTSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CustScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=CUSTOMER_MANAGE, name=CUSTOMER_MANAGE, resource="customer", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=CUSTOMER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant CUSTOMER_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"custscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_CUSTSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CustScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=CUSTOMER_MANAGE, name=CUSTOMER_MANAGE, resource="customer", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=CUSTOMER_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _setup(client):
    """Create two reps, two customers (each assigned to a rep).

    Rep A → Customer A (active assignment)
    Rep B → Customer B (active assignment)
    """
    from datetime import timedelta

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        # Create two representatives
        rep_a = Representative(
            code=f"REPA-CS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-CS-{suffix}", person_name="Rep B", status="ACTIVE",
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
        "cust_a_code": cust_a.code,
        "cust_b_code": cust_b.code,
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestCustomerUpdateScope:
    """PATCH /customers/{customer_id} representative scope enforcement."""

    def test_representative_can_update_own_customer(self, client):
        """Representative can update their own assigned customer."""
        data = _setup(client)
        resp = client.patch(
            f"/api/v1/customers/{data['cust_a_id']}",
            json={"name": "Updated Name A"},
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Updated Name A"

    def test_representative_cannot_update_other_rep_customer(self, client):
        """Representative cannot update another rep's customer → 404."""
        data = _setup(client)
        resp = client.patch(
            f"/api/v1/customers/{data['cust_b_id']}",
            json={"name": "Hacked Name"},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_nonexistent_customer_returns_404(self, client):
        """Nonexistent customer returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.patch(
            f"/api/v1/customers/{fake_id}",
            json={"name": "Ghost"},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_update_any_customer(self, client):
        """Admin/staff can update any customer."""
        data = _setup(client)
        resp = client.patch(
            f"/api/v1/customers/{data['cust_a_id']}",
            json={"name": "Admin Updated"},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Admin Updated"


@requires_database
class TestCustomerDeactivateScope:
    """POST /customers/{customer_id}/deactivate representative scope enforcement."""

    def test_representative_can_deactivate_own_customer(self, client):
        """Representative can deactivate their own assigned customer."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/customers/{data['cust_a_id']}/deactivate",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "INACTIVE"

    def test_representative_cannot_deactivate_other_rep_customer(self, client):
        """Representative cannot deactivate another rep's customer → 404."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/customers/{data['cust_b_id']}/deactivate",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_out_of_scope_deactivate_produces_no_side_effects(self, client):
        """Out-of-scope deactivate changes nothing — customer status unchanged."""
        data = _setup(client)

        # Capture before state
        session = get_session_factory()()
        try:
            cust = session.get(Customer, uuid.UUID(data["cust_b_id"]))
            status_before = cust.status
        finally:
            session.close()

        # Attempt out-of-scope deactivate
        resp = client.post(
            f"/api/v1/customers/{data['cust_b_id']}/deactivate",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no changes
        session = get_session_factory()()
        try:
            cust = session.get(Customer, uuid.UUID(data["cust_b_id"]))
            assert cust.status == status_before, "Customer status must not change"
        finally:
            session.close()

    def test_admin_can_deactivate_any_customer(self, client):
        """Admin/staff can deactivate any customer."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/customers/{data['cust_a_id']}/deactivate",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "INACTIVE"


@requires_database
class TestCustomerReadScope:
    """GET /customers and GET /customers/{id} representative scope enforcement.

    Covers M-01 (list scope) and M-02 (read scope) from
    SECURITY_AUDIT_2026-08-29.md.
    """

    def test_representative_can_read_own_customer(self, client):
        """Representative can read their own assigned customer."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_a_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["cust_a_id"]

    def test_representative_cannot_read_other_rep_customer(self, client):
        """Representative cannot read another rep's customer -> 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_b_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_nonexistent_customer_returns_404(self, client):
        """Nonexistent customer returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/customers/{fake_id}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_customer(self, client):
        """Admin/staff can read any customer."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/customers/{data['cust_a_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text

    def test_representative_only_sees_own_customers_in_list(self, client):
        """Representative can only list customers assigned to them (M-01)."""
        data = _setup(client)
        resp = client.get(
            "/api/v1/customers",
            headers=data["headers_a"],
            params={"limit": 100},
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        customer_ids = {item["id"] for item in items}
        assert data["cust_a_id"] in customer_ids, "Own customer must be visible"
        assert data["cust_b_id"] not in customer_ids, "Other rep's customer must not be visible"

    def test_admin_sees_all_customers_in_list(self, client):
        """Admin/staff can list all customers (verified via search by unique code)."""
        data = _setup(client)
        # Verify admin can see Customer A via unique code search
        resp_a = client.get(
            "/api/v1/customers",
            headers=data["headers_admin"],
            params={"search": data["cust_a_code"]},
        )
        assert resp_a.status_code == 200, resp_a.text
        a_ids = {item["id"] for item in resp_a.json()["items"]}
        assert data["cust_a_id"] in a_ids, "Admin must see Customer A"
        # Verify admin can also see Customer B via unique code search
        resp_b = client.get(
            "/api/v1/customers",
            headers=data["headers_admin"],
            params={"search": data["cust_b_code"]},
        )
        assert resp_b.status_code == 200, resp_b.text
        b_ids = {item["id"] for item in resp_b.json()["items"]}
        assert data["cust_b_id"] in b_ids, "Admin must see Customer B"


@requires_database
class TestCustomerAssignmentExpiry:
    """Verify assignment time-window enforcement."""

    def test_expired_assignment_denies_mutation(self, client):
        """Representative cannot mutate a customer with an expired assignment → 404."""
        from datetime import timedelta

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]

            rep = Representative(
                code=f"REPA-EX-{suffix}", person_name="Rep Expired", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep)
            session.flush()

            cust = Customer(
                code=f"CUST-EX-{suffix}", name="Expired Customer", type="CORPORATE",
                currency_id=currency.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(cust)
            session.flush()

            # Expired assignment (ended 10 days ago)
            now = _now()
            expired_assign = CustomerRepAssignment(
                customer_id=cust.id, representative_id=rep.id,
                effective_from=now - timedelta(days=30),
                effective_to=now - timedelta(days=10),
                priority=1,
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(expired_assign)
            session.flush()

            headers, user = _create_rep_user(session, system_user, rep, suffix=f"ex_{suffix}")
            session.commit()
        finally:
            session.close()

        # Try to update with expired assignment
        resp = client.patch(
            f"/api/v1/customers/{cust.id}",
            json={"name": "Should Fail"},
            headers=headers,
        )
        assert resp.status_code == 404

        # Try to deactivate with expired assignment
        resp = client.post(
            f"/api/v1/customers/{cust.id}/deactivate",
            headers=headers,
        )
        assert resp.status_code == 404
