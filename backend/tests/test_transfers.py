"""Tests for the stock transfer endpoints and service.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_invoices.py``).  Builds its
own supporting rows (currency, warehouse, uom, product, price_history,
representative, customer, order + shipped order for inventory) directly
via the ORM/service layer.

Test matrix:
* Happy path: create -> dispatch (TRANSFER_OUT) -> receive (TRANSFER_IN)
  with inventory balance checks before and after each step.
* Cancel from DRAFT succeeds.
* Double-dispatch of already-DISPATCHED transfer is rejected (409).
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
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB transfer tests",
)

TRANSFER_MANAGE = "TRANSFER_MANAGE"


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


def _user_with_permissions(*permission_codes: str) -> dict[str, str]:
    """Create a fresh user, grant it every permission code given, log in."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_trf_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"TRANSFER_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Transfer Tester (test)", created_by=system_user.id
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session,
                    code=code,
                    name=code,
                    resource="stock_transfer",
                    action="test",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=code
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@pytest.fixture()
def manage_auth_headers() -> dict[str, str]:
    return _user_with_permissions(TRANSFER_MANAGE)


@pytest.fixture()
def transfer_fixtures() -> dict:
    """All supporting rows for creating a transfer: two warehouses,
    a product with inventory at the source warehouse."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        source_warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]
        # Create a second warehouse (destination).
        dest_warehouse = Warehouse(
            code=f"DEST-{suffix}",
            name=f"Destination Warehouse {suffix}",
            type="REPRESENTATIVE",
            ownership_mode="OWNED",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(dest_warehouse)
        session.flush()

        # Create a product.
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        product = Product(
            sku=f"SKU-TRF-{suffix}",
            name="Transfer Test Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        # Post initial inventory at the source warehouse.
        from services import inventory_service

        inventory_service.post_transaction(
            session,
            product_id=product.id,
            warehouse_id=source_warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"),
            currency_id=currency.id,
            actor_user_id=system_user.id,
        )

        session.commit()

        return {
            "currency_id": str(currency.id),
            "source_warehouse_id": str(source_warehouse.id),
            "dest_warehouse_id": str(dest_warehouse.id),
            "product_id": str(product.id),
        }
    finally:
        session.close()


# ----------------------------------------------------------------- Tests


@requires_database
def test_happy_path_dispatch_receive(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    transfer_fixtures: dict,
) -> None:
    """Full happy path: create -> dispatch -> receive, with inventory
    balance checks at each step."""
    from services import inventory_service

    source_wh_id = uuid.UUID(transfer_fixtures["source_warehouse_id"])
    dest_wh_id = uuid.UUID(transfer_fixtures["dest_warehouse_id"])
    product_id = uuid.UUID(transfer_fixtures["product_id"])

    # -- Pre-transfer balance check via service layer --
    session = get_session_factory()()
    try:
        source_before = inventory_service.get_balance(
            session, warehouse_id=source_wh_id, product_id=product_id
        )
        dest_before = inventory_service.get_balance(
            session, warehouse_id=dest_wh_id, product_id=product_id
        )
    finally:
        session.close()

    assert source_before == decimal.Decimal("1000"), f"Expected 1000, got {source_before}"
    assert dest_before == decimal.Decimal("0"), f"Expected 0, got {dest_before}"

    # -- Create transfer --
    resp = client.post(
        "/api/v1/transfers",
        json={
            "source_warehouse_id": transfer_fixtures["source_warehouse_id"],
            "destination_warehouse_id": transfer_fixtures["dest_warehouse_id"],
            "lines": [
                {
                    "product_id": transfer_fixtures["product_id"],
                    "qty_requested": "100",
                    "unit_cost": "50.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "DRAFT"
    transfer_id = body["id"]
    assert len(body["lines"]) == 1

    # -- Submit: DRAFT -> PENDING --
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/submit",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "PENDING"

    # -- Approve: PENDING -> APPROVED --
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/approve",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "APPROVED"

    # -- Dispatch: APPROVED -> DISPATCHED --
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/dispatch",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "DISPATCHED"
    assert resp.json()["dispatched_at"] is not None

    # -- Post-dispatch balance: source debited, dest still 0 --
    session = get_session_factory()()
    try:
        source_after_dispatch = inventory_service.get_balance(
            session, warehouse_id=source_wh_id, product_id=product_id
        )
        dest_after_dispatch = inventory_service.get_balance(
            session, warehouse_id=dest_wh_id, product_id=product_id
        )
    finally:
        session.close()

    assert source_after_dispatch == decimal.Decimal("900"), (
        f"Expected 900 after dispatch, got {source_after_dispatch}"
    )
    assert dest_after_dispatch == decimal.Decimal("0"), (
        f"Expected 0 at dest after dispatch, got {dest_after_dispatch}"
    )

    # -- Receive: DISPATCHED -> RECEIVED --
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/receive",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "RECEIVED"
    assert resp.json()["received_at"] is not None

    # -- Post-receive balance: source still 900, dest now 100 --
    session = get_session_factory()()
    try:
        source_after_receive = inventory_service.get_balance(
            session, warehouse_id=source_wh_id, product_id=product_id
        )
        dest_after_receive = inventory_service.get_balance(
            session, warehouse_id=dest_wh_id, product_id=product_id
        )
    finally:
        session.close()

    assert source_after_receive == decimal.Decimal("900"), (
        f"Expected 900 after receive, got {source_after_receive}"
    )
    assert dest_after_receive == decimal.Decimal("100"), (
        f"Expected 100 at dest after receive, got {dest_after_receive}"
    )


@requires_database
def test_cancel_from_draft(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    transfer_fixtures: dict,
) -> None:
    """Cancel a DRAFT transfer succeeds."""
    resp = client.post(
        "/api/v1/transfers",
        json={
            "source_warehouse_id": transfer_fixtures["source_warehouse_id"],
            "destination_warehouse_id": transfer_fixtures["dest_warehouse_id"],
            "lines": [
                {
                    "product_id": transfer_fixtures["product_id"],
                    "qty_requested": "50",
                    "unit_cost": "50.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    transfer_id = resp.json()["id"]

    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/cancel",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "CANCELLED"


@requires_database
def test_double_dispatch_rejected(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    transfer_fixtures: dict,
) -> None:
    """Dispatching an already-DISPATCHED transfer must be rejected (409)."""
    resp = client.post(
        "/api/v1/transfers",
        json={
            "source_warehouse_id": transfer_fixtures["source_warehouse_id"],
            "destination_warehouse_id": transfer_fixtures["dest_warehouse_id"],
            "lines": [
                {
                    "product_id": transfer_fixtures["product_id"],
                    "qty_requested": "10",
                    "unit_cost": "50.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    transfer_id = resp.json()["id"]

    # Submit + Approve before dispatch.
    client.post(
        f"/api/v1/transfers/{transfer_id}/submit",
        json={},
        headers=manage_auth_headers,
    )
    client.post(
        f"/api/v1/transfers/{transfer_id}/approve",
        json={},
        headers=manage_auth_headers,
    )

    # First dispatch -- succeeds.
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/dispatch",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Second dispatch -- must fail (DISPATCHED is not in ALLOWED_TRANSITIONS["DISPATCHED"]).
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/dispatch",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 409, resp.text
