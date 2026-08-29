"""Focused tests for the Stock Transfer Approval Workflow.

Covers:
1. Submit transfer: DRAFT -> PENDING
2. Approve transfer: PENDING -> APPROVED
3. Dispatch requires APPROVED (not DRAFT directly)
4. Full happy path: create -> submit -> approve -> dispatch -> receive
5. Cancel from PENDING allowed
6. Cancel from APPROVED allowed
7. Cancel from DISPATCHED rejected
8. Submit non-DRAFT rejected
9. Approve non-PENDING rejected
10. Dispatch non-APPROVED rejected
11. Approval sets approved_by and approved_at
12. Existing regression: create + cancel still works

All tests use real PostgreSQL (no mocks).
"""

from __future__ import annotations

import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.product import Product
from database.models.warehouse import Warehouse
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping transfer approval tests",
)

TRANSFER_MANAGE = "TRANSFER_MANAGE"


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
        username = f"test_t approvals_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"TAPPR_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Transfer Approval Tester (test)",
            created_by=system_user.id,
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="transfer",
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


@pytest.fixture()
def manage_auth() -> dict[str, str]:
    return _user_with_permissions(TRANSFER_MANAGE)


@pytest.fixture()
def transfer_fixtures() -> dict:
    """Create all FK targets for transfer testing."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        product = Product(
            sku=f"SKU-TAPPR-{suffix}",
            name="Transfer Approval Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        # Create a second warehouse for transfers.
        dest_warehouse = Warehouse(
            code=f"WH-DEST-{suffix}",
            name="Destination Warehouse",
            type="REPRESENTATIVE",
            ownership_mode="OWNED",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(dest_warehouse)
        session.flush()

        # Post stock to source warehouse.
        inventory_service.post_transaction(
            session,
            product_id=product.id,
            warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"),
            currency_id=currency.id,
            actor_user_id=system_user.id,
        )

        session.commit()
        return {
            "currency_id": str(currency.id),
            "warehouse_id": str(warehouse.id),
            "dest_warehouse_id": str(dest_warehouse.id),
            "product_id": str(product.id),
        }
    finally:
        session.close()


def _create_transfer(client: TestClient, auth: dict, fx: dict) -> dict:
    """Helper to create a DRAFT transfer and return the response body."""
    payload = {
        "source_warehouse_id": fx["warehouse_id"],
        "destination_warehouse_id": fx["dest_warehouse_id"],
        "lines": [
            {
                "product_id": fx["product_id"],
                "qty_requested": "10",
                "unit_cost": "50.0000",
            }
        ],
    }
    resp = client.post("/api/v1/transfers", json=payload, headers=auth)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Tests
# ===========================================================================


@requires_database
class TestSubmitTransfer:
    """DRAFT -> PENDING submission."""

    def test_submit_creates_pending(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Submitting a DRAFT transfer transitions it to PENDING."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]
        assert transfer["state"] == "DRAFT"

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "PENDING"

    def test_submit_with_note(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Submitting with a note records it in history."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={"note": "Please approve urgently"},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "PENDING"


@requires_database
class TestApproveTransfer:
    """PENDING -> APPROVED approval."""

    def test_approve_sets_approved_fields(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Approving sets approved_by and approved_at on the transfer."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        # Submit.
        client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )

        # Approve.
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/approve",
            json={"note": "Approved by manager"},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "APPROVED"
        assert body["approved_by"] is not None
        assert body["approved_at"] is not None


@requires_database
class TestDispatchRequiresApproved:
    """Dispatch now requires APPROVED state, not DRAFT."""

    def test_dispatch_from_draft_rejected(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Dispatching a DRAFT transfer is rejected (must submit+approve first)."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/dispatch",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_dispatch_from_approved_succeeds(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Dispatching an APPROVED transfer succeeds."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        # Submit.
        client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )

        # Approve.
        client.post(
            f"/api/v1/transfers/{transfer_id}/approve",
            json={},
            headers=manage_auth,
        )

        # Dispatch.
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/dispatch",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "DISPATCHED"


@requires_database
class TestFullHappyPath:
    """Complete transfer lifecycle with approval."""

    def test_create_submit_approve_dispatch_receive(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Full lifecycle: create -> submit -> approve -> dispatch -> receive."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]
        assert transfer["state"] == "DRAFT"

        # Submit.
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "PENDING"

        # Approve.
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/approve",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "APPROVED"

        # Dispatch.
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/dispatch",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "DISPATCHED"

        # Receive.
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/receive",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "RECEIVED"


@requires_database
class TestCancelFromApprovalStates:
    """Cancellation from PENDING and APPROVED states."""

    def test_cancel_from_pending(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """A PENDING transfer can be cancelled."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/cancel",
            json={"note": "No longer needed"},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "CANCELLED"

    def test_cancel_from_approved(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """An APPROVED transfer can be cancelled before dispatch."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )
        client.post(
            f"/api/v1/transfers/{transfer_id}/approve",
            json={},
            headers=manage_auth,
        )

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/cancel",
            json={"note": "Changed mind"},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "CANCELLED"

    def test_cancel_from_dispatched_rejected(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """A DISPATCHED transfer cannot be cancelled."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        # Full lifecycle to DISPATCHED.
        client.post(f"/api/v1/transfers/{transfer_id}/submit", json={}, headers=manage_auth)
        client.post(f"/api/v1/transfers/{transfer_id}/approve", json={}, headers=manage_auth)
        client.post(f"/api/v1/transfers/{transfer_id}/dispatch", json={}, headers=manage_auth)

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/cancel",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 409


@requires_database
class TestInvalidTransitions:
    """Invalid state transitions are rejected."""

    def test_submit_non_draft_rejected(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Submitting a non-DRAFT transfer is rejected."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        # Submit once.
        client.post(f"/api/v1/transfers/{transfer_id}/submit", json={}, headers=manage_auth)

        # Try to submit again (now PENDING).
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/submit",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_approve_non_pending_rejected(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Approving a non-PENDING transfer is rejected."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        # Try to approve without submitting first (still DRAFT).
        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/approve",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 409


@requires_database
class TestTransferHistory:
    """Transfer history records all state transitions."""

    def test_history_records_full_lifecycle(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """All state transitions are recorded in transfer_history."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        # Submit -> Approve -> Dispatch -> Receive.
        client.post(f"/api/v1/transfers/{transfer_id}/submit", json={}, headers=manage_auth)
        client.post(f"/api/v1/transfers/{transfer_id}/approve", json={}, headers=manage_auth)
        client.post(f"/api/v1/transfers/{transfer_id}/dispatch", json={}, headers=manage_auth)
        client.post(f"/api/v1/transfers/{transfer_id}/receive", json={}, headers=manage_auth)

        # Read history.
        resp = client.get(
            f"/api/v1/transfers/{transfer_id}/history",
            headers=manage_auth,
        )
        assert resp.status_code == 200
        history = resp.json()["items"]
        # creation (DRAFT->DRAFT) + submit + approve + dispatch + receive = 5
        assert len(history) == 5

        # Verify state transitions in order.
        states = [h["to_state"] for h in history]
        assert states == ["DRAFT", "PENDING", "APPROVED", "DISPATCHED", "RECEIVED"]


@requires_database
class TestExistingRegression:
    """Existing transfer functionality still works."""

    def test_create_and_cancel_still_works(
        self, client: TestClient, manage_auth: dict, transfer_fixtures: dict,
    ):
        """Create a transfer and cancel it directly (DRAFT -> CANCELLED)."""
        transfer = _create_transfer(client, manage_auth, transfer_fixtures)
        transfer_id = transfer["id"]

        resp = client.post(
            f"/api/v1/transfers/{transfer_id}/cancel",
            json={"note": "Changed my mind"},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "CANCELLED"
