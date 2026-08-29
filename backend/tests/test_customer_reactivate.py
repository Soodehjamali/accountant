"""Tests for customer reactivation: service layer and endpoint.

Covers:
1. reactivate_customer sets status to ACTIVE.
2. reactivate_customer raises CustomerAlreadyActiveError for ACTIVE customer.
3. reactivate_customer raises CustomerNotFoundError for deleted customer.
4. POST /customers/{id}/reactivate endpoint works end-to-end.
5. Endpoint requires CUSTOMER_MANAGE permission.
6. Endpoint enforces representative scope.

All tests use real PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
from services.customer_service import (
    CustomerAlreadyActiveError,
    CustomerNotFoundError,
    deactivate_customer,
    get_customer,
    reactivate_customer,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping customer reactivation tests",
)

CUSTOMER_MANAGE = "CUSTOMER_MANAGE"


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------

@requires_database
class TestReactivateCustomerService:
    """Direct service function tests."""

    def _setup(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            cust = Customer(
                code=f"CREACT-{suffix}",
                name="Reactivate Test",
                type="INDIVIDUAL",
                currency_id=currency.id,
                status="INACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(cust)
            session.flush()
            session.commit()
            return cust.id, system_user.id
        finally:
            session.close()

    def test_reactivate_sets_active(self):
        customer_id, actor_id = self._setup()
        session = get_session_factory()()
        try:
            customer = reactivate_customer(session, customer_id, updated_by=actor_id)
            session.commit()
            assert customer.status == "ACTIVE"
        finally:
            session.close()

    def test_already_active_raises(self):
        customer_id, actor_id = self._setup()
        session = get_session_factory()()
        try:
            # First reactivate to ACTIVE
            reactivate_customer(session, customer_id, updated_by=actor_id)
            session.commit()
        finally:
            session.close()

        session = get_session_factory()()
        try:
            with pytest.raises(CustomerAlreadyActiveError):
                reactivate_customer(session, customer_id, updated_by=actor_id)
        finally:
            session.close()

    def test_nonexistent_raises(self):
        session = get_session_factory()()
        try:
            fake_id = uuid.uuid4()
            system_user = bootstrap_service.ensure_system_user(session)
            with pytest.raises(CustomerNotFoundError):
                reactivate_customer(session, fake_id, updated_by=system_user.id)
        finally:
            session.close()

    def test_deactivate_then_reactivate_roundtrip(self):
        customer_id, actor_id = self._setup()
        session = get_session_factory()()
        try:
            # Should already be INACTIVE from setup
            cust = get_customer(session, customer_id)
            assert cust.status == "INACTIVE"
            # Reactivate
            cust = reactivate_customer(session, customer_id, updated_by=actor_id)
            assert cust.status == "ACTIVE"
            # Deactivate again
            cust = deactivate_customer(session, customer_id, updated_by=actor_id)
            assert cust.status == "INACTIVE"
            session.commit()
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Endpoint-level tests
# ---------------------------------------------------------------------------

def _login(username: str, password: str) -> dict[str, str]:
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session, username_or_email=username, password=password
        )
        assert user is not None
        session.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session.close()
    return {"Authorization": f"Bearer {token}"}


def _make_manage_user(suffix: str):
    """Create a user with CUSTOMER_MANAGE, return (auth_headers, user_id, session)."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        username = f"react_test_{suffix}"
        password = "correct-horse-battery-staple"
        user = auth_service.create_user(
            session, username=username, email=f"{username}@example.invalid",
            password=password, created_by=system_user.id,
        )
        role_code = f"ROLE_REACT_{suffix}"
        rbac_service.create_role(session, code=role_code, name=f"React {suffix}",
                                 created_by=system_user.id)
        try:
            rbac_service.create_permission(
                session, code=CUSTOMER_MANAGE, name=CUSTOMER_MANAGE,
                resource="customer", action="manage", created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(session, role_code=role_code,
                                              permission_code=CUSTOMER_MANAGE)
        rbac_service.assign_role(session, user_id=user.id, role_code=role_code,
                                 assigned_by=system_user.id)
        session.commit()
        return _login(username, password), user.id, session
    finally:
        session.close()


def _make_inactive_customer(suffix: str) -> Customer:
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        cust = Customer(
            code=f"CREACT-EP-{suffix}", name="Endpoint Reactivate",
            type="CORPORATE", currency_id=currency.id, status="INACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(cust)
        session.flush()
        session.commit()
        return cust
    finally:
        session.close()


@requires_database
class TestReactivateEndpoint:
    """POST /customers/{id}/reactivate endpoint tests."""

    def test_reactivate_success(self, client: TestClient):
        suffix = uuid.uuid4().hex[:8]
        headers, _, session = _make_manage_user(suffix)
        cust = _make_inactive_customer(suffix)
        try:
            resp = client.post(
                f"/api/v1/customers/{cust.id}/reactivate", headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "ACTIVE"
        finally:
            session.close()

    def test_already_active_returns_409(self, client: TestClient):
        suffix = uuid.uuid4().hex[:8]
        headers, _, session = _make_manage_user(suffix)
        cust = _make_inactive_customer(suffix)
        try:
            # Reactivate first
            resp = client.post(
                f"/api/v1/customers/{cust.id}/reactivate", headers=headers,
            )
            assert resp.status_code == 200
            # Try again -> 409
            resp = client.post(
                f"/api/v1/customers/{cust.id}/reactivate", headers=headers,
            )
            assert resp.status_code == 409
        finally:
            session.close()

    def test_requires_permission(self, client: TestClient):
        suffix = uuid.uuid4().hex[:8]
        # Create a user without CUSTOMER_MANAGE
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            username = f"plain_react_{suffix}"
            auth_service.create_user(
                session, username=username, email=f"{username}@example.invalid",
                password="correct-horse-battery-staple", created_by=system_user.id,
            )
            session.commit()
        finally:
            session.close()

        plain_headers = _login(username, "correct-horse-battery-staple")
        cust = _make_inactive_customer(suffix)
        resp = client.post(
            f"/api/v1/customers/{cust.id}/reactivate", headers=plain_headers,
        )
        assert resp.status_code == 403

    def test_scope_enforced(self, client: TestClient):
        """Representative cannot reactivate another rep's customer."""
        suffix = uuid.uuid4().hex[:8]
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

            # Create rep A and rep B
            rep_a = Representative(
                code=f"REPA-REACT-{suffix}", person_name="Rep A",
                status="ACTIVE", created_by=system_user.id,
                updated_by=system_user.id,
            )
            rep_b = Representative(
                code=f"REPB-REACT-{suffix}", person_name="Rep B",
                status="ACTIVE", created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add_all([rep_a, rep_b])
            session.flush()

            # Customer assigned to rep B only
            cust = Customer(
                code=f"CREACT-SCOPE-{suffix}", name="Scope Customer",
                type="CORPORATE", currency_id=currency.id, status="INACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(cust)
            session.flush()

            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            assignment = CustomerRepAssignment(
                customer_id=cust.id, representative_id=rep_b.id,
                effective_from=now, effective_to=now + timedelta(days=365),
                priority=1, created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(assignment)
            session.flush()

            # Create user linked to rep A with CUSTOMER_MANAGE
            from security import create_access_token
            from app.core.config import get_settings
            settings = get_settings()

            username = f"scope_react_{suffix}"
            user = auth_service.create_user(
                session, username=username, email=f"{username}@example.invalid",
                password="correct-horse-battery-staple", created_by=system_user.id,
            )
            user.representative_id = rep_a.id
            session.flush()

            role_code = f"ROLE_SR_{suffix}"
            rbac_service.create_role(session, code=role_code,
                                     name=f"ScopeReact {suffix}",
                                     created_by=system_user.id)
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=CUSTOMER_MANAGE)
            rbac_service.assign_role(session, user_id=user.id,
                                     role_code=role_code,
                                     assigned_by=system_user.id)
            session.commit()

            token = create_access_token(
                subject=str(user.id), secret_key=settings.secret_key,
                expires_in_seconds=settings.access_token_expire_minutes * 60,
            )
            headers_a = {"Authorization": f"Bearer {token}"}
        finally:
            session.close()

        resp = client.post(
            f"/api/v1/customers/{cust.id}/reactivate", headers=headers_a,
        )
        # Out-of-scope -> 404 (same as other customer scope checks)
        assert resp.status_code == 404
