"""Regression tests for GET /representatives/{id}/commission-balance scope.

Verifies:
1. Admin/staff users can query any representative's balance.
2. Representative-linked users can only query their own balance (403 for cross-rep).
3. Nonexistent representative returns 404 (not 200 with zero balance).
4. Representative-linked users can query their own balance successfully.
"""

from __future__ import annotations

import os
import uuid

import pytest

from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping commission balance scope tests",
)

COMMISSION_MANAGE = "COMMISSION_MANAGE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(username: str, password: str) -> dict[str, str]:
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session, username_or_email=username, password=password,
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


def _user_with_permissions(*permission_codes: str) -> dict[str, str]:
    """Create a fresh user, grant it every permission code given, log in."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_bal_scope_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"BAL_SCOPE_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Balance Scope Tester (test)",
            created_by=system_user.id,
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="commission",
                    action="test", created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=code,
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code,
            assigned_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


def _rep_linked_user(*permission_codes: str, representative_id: uuid.UUID) -> dict[str, str]:
    """Create a user linked to a representative, grant permissions, log in."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_bal_rep_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        # Link user to the representative.
        from database.models.app_user import AppUser
        from sqlalchemy import update
        session.execute(
            update(AppUser).where(AppUser.id == new_user.id).values(
                representative_id=representative_id,
            )
        )
        session.flush()

        role_code = f"BAL_REP_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Balance Rep Scope Tester (test)",
            created_by=system_user.id,
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="commission",
                    action="test", created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=code,
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code,
            assigned_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@requires_database
class TestCommissionBalanceScope:
    """Scope enforcement for GET /representatives/{id}/commission-balance."""

    @pytest.fixture()
    def admin_auth(self) -> dict[str, str]:
        return _user_with_permissions(COMMISSION_MANAGE)

    @pytest.fixture()
    def representative_ids(self) -> tuple[str, str]:
        """Create two distinct representatives and return their IDs."""
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            suffix_a = uuid.uuid4().hex[:8]
            suffix_b = uuid.uuid4().hex[:8]

            rep_a = Representative(
                code=f"REP-BSA-{suffix_a}",
                person_name="Balance Scope Rep A",
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(rep_a)

            rep_b = Representative(
                code=f"REP-BSB-{suffix_b}",
                person_name="Balance Scope Rep B",
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(rep_b)
            session.commit()
            return (str(rep_a.id), str(rep_b.id))
        finally:
            session.close()

    def test_admin_can_query_any_representative_balance(
        self, client, admin_auth: dict, representative_ids: tuple[str, str],
    ):
        """Admin users (no representative link) can query any representative."""
        rep_id = representative_ids[0]
        resp = client.get(
            f"/api/v1/representatives/{rep_id}/commission-balance",
            headers=admin_auth,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["representative_id"] == rep_id
        assert "balance" in body

    def test_rep_can_query_own_balance(
        self, client, representative_ids: tuple[str, str],
    ):
        """A representative-linked user can query their own commission balance."""
        rep_id = representative_ids[0]
        rep_auth = _rep_linked_user(
            COMMISSION_MANAGE, representative_id=uuid.UUID(rep_id),
        )
        resp = client.get(
            f"/api/v1/representatives/{rep_id}/commission-balance",
            headers=rep_auth,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["representative_id"] == rep_id

    def test_rep_cannot_query_other_rep_balance(
        self, client, representative_ids: tuple[str, str],
    ):
        """A representative-linked user gets 403 when querying another rep's balance."""
        rep_a_id, rep_b_id = representative_ids
        # User linked to rep_a tries to query rep_b's balance.
        rep_auth = _rep_linked_user(
            COMMISSION_MANAGE, representative_id=uuid.UUID(rep_a_id),
        )
        resp = client.get(
            f"/api/v1/representatives/{rep_b_id}/commission-balance",
            headers=rep_auth,
        )
        assert resp.status_code == 403
        assert "own commission balance" in resp.json()["detail"]

    def test_nonexistent_representative_returns_404(
        self, client, admin_auth: dict,
    ):
        """A genuinely nonexistent representative_id returns 404."""
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/representatives/{fake_id}/commission-balance",
            headers=admin_auth,
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_unauthenticated_returns_401(
        self, client, representative_ids: tuple[str, str],
    ):
        """Unauthenticated requests are rejected."""
        rep_id = representative_ids[0]
        resp = client.get(
            f"/api/v1/representatives/{rep_id}/commission-balance",
        )
        assert resp.status_code in (401, 403)
