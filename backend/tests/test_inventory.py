"""Tests for the inventory ledger: ``services.inventory_service`` and the
``/api/v1/inventory/*`` endpoints.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_auth.py`` / ``test_products.py``) -- these exercise
the real service/DB, not a mock, since the invariants under test (sign
matching, no-negative-stock, hash chaining) only mean something against a
real, persisted sequence of rows.
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
    reason="DATABASE_URL is not set; skipping live DB inventory tests",
)


@pytest.fixture()
def ledger_context() -> dict[str, uuid.UUID]:
    """Seed everything a ledger post needs: system user, default UoM,
    default currency, default warehouse, movement types, and one fresh
    product -- returns their ids in a dict."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        from services import product_service

        sku = f"INV-TEST-{uuid.uuid4().hex[:8]}"
        product = product_service.create_product(
            session,
            sku=sku,
            name="Inventory Test Widget",
            base_uom_id=uom.id,
            created_by=system_user.id,
        )
        session.commit()
        return {
            "actor_user_id": system_user.id,
            "currency_id": currency.id,
            "warehouse_id": warehouse.id,
            "product_id": product.id,
        }
    finally:
        session.close()


@requires_database
def test_post_transaction_increases_balance(ledger_context: dict[str, uuid.UUID]) -> None:
    session = get_session_factory()()
    try:
        inventory_service.post_transaction(
            session,
            product_id=ledger_context["product_id"],
            warehouse_id=ledger_context["warehouse_id"],
            movement_type_code="RECEIPT_FROM_PRODUCTION",
            signed_quantity=decimal.Decimal("50"),
            unit_cost=decimal.Decimal("10"),
            currency_id=ledger_context["currency_id"],
            actor_user_id=ledger_context["actor_user_id"],
        )
        session.commit()

        balance = inventory_service.get_balance(
            session,
            warehouse_id=ledger_context["warehouse_id"],
            product_id=ledger_context["product_id"],
        )
        assert balance == decimal.Decimal("50")
    finally:
        session.close()


@requires_database
def test_post_transaction_rejects_sign_mismatch(
    ledger_context: dict[str, uuid.UUID]
) -> None:
    session = get_session_factory()()
    try:
        with pytest.raises(inventory_service.MovementTypeSignMismatchError):
            inventory_service.post_transaction(
                session,
                product_id=ledger_context["product_id"],
                warehouse_id=ledger_context["warehouse_id"],
                # SALE_OUT is sign -1; a positive quantity must be rejected.
                movement_type_code="SALE_OUT",
                signed_quantity=decimal.Decimal("10"),
                unit_cost=decimal.Decimal("10"),
                currency_id=ledger_context["currency_id"],
                actor_user_id=ledger_context["actor_user_id"],
            )
    finally:
        session.close()


@requires_database
def test_post_transaction_rejects_negative_balance(
    ledger_context: dict[str, uuid.UUID]
) -> None:
    session = get_session_factory()()
    try:
        with pytest.raises(inventory_service.NegativeStockError):
            inventory_service.post_transaction(
                session,
                product_id=ledger_context["product_id"],
                warehouse_id=ledger_context["warehouse_id"],
                movement_type_code="SALE_OUT",
                signed_quantity=decimal.Decimal("-5"),
                unit_cost=decimal.Decimal("10"),
                currency_id=ledger_context["currency_id"],
                actor_user_id=ledger_context["actor_user_id"],
            )
    finally:
        session.close()


@requires_database
def test_reverse_transaction_restores_balance(
    ledger_context: dict[str, uuid.UUID]
) -> None:
    session = get_session_factory()()
    try:
        original = inventory_service.post_transaction(
            session,
            product_id=ledger_context["product_id"],
            warehouse_id=ledger_context["warehouse_id"],
            movement_type_code="RECEIPT_FROM_PRODUCTION",
            signed_quantity=decimal.Decimal("30"),
            unit_cost=decimal.Decimal("10"),
            currency_id=ledger_context["currency_id"],
            actor_user_id=ledger_context["actor_user_id"],
        )
        session.commit()

        inventory_service.reverse_transaction(
            session, original.id, actor_user_id=ledger_context["actor_user_id"]
        )
        session.commit()

        balance = inventory_service.get_balance(
            session,
            warehouse_id=ledger_context["warehouse_id"],
            product_id=ledger_context["product_id"],
        )
        assert balance == decimal.Decimal("0")

        with pytest.raises(inventory_service.AlreadyReversedError):
            inventory_service.reverse_transaction(
                session, original.id, actor_user_id=ledger_context["actor_user_id"]
            )
    finally:
        session.close()


@requires_database
def test_hash_chain_links_consecutive_rows(
    ledger_context: dict[str, uuid.UUID]
) -> None:
    session = get_session_factory()()
    try:
        first = inventory_service.post_transaction(
            session,
            product_id=ledger_context["product_id"],
            warehouse_id=ledger_context["warehouse_id"],
            movement_type_code="RECEIPT_FROM_PRODUCTION",
            signed_quantity=decimal.Decimal("20"),
            unit_cost=decimal.Decimal("10"),
            currency_id=ledger_context["currency_id"],
            actor_user_id=ledger_context["actor_user_id"],
        )
        second = inventory_service.post_transaction(
            session,
            product_id=ledger_context["product_id"],
            warehouse_id=ledger_context["warehouse_id"],
            movement_type_code="RECEIPT_FROM_PRODUCTION",
            signed_quantity=decimal.Decimal("5"),
            unit_cost=decimal.Decimal("10"),
            currency_id=ledger_context["currency_id"],
            actor_user_id=ledger_context["actor_user_id"],
        )
        session.commit()

        assert second.prev_hash == first.row_hash
        assert second.sequence_no == first.sequence_no + 1
    finally:
        session.close()


# --------------------------------------------------------------------- API


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_inventory_{suffix}"
        password = "correct-horse-battery-staple"
        auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session2 = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session2, username_or_email=username, password=password
        )
        assert user is not None
        session2.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session2.close()

    return {"Authorization": f"Bearer {token}"}


@requires_database
def test_api_post_transaction_and_get_balance(
    client: TestClient,
    auth_headers: dict[str, str],
    ledger_context: dict[str, uuid.UUID],
) -> None:
    response = client.post(
        "/api/v1/inventory/transactions",
        json={
            "product_id": str(ledger_context["product_id"]),
            "warehouse_id": str(ledger_context["warehouse_id"]),
            "movement_type_code": "RECEIPT_FROM_PRODUCTION",
            "signed_quantity": "15",
            "unit_cost": "9.5",
            "currency_id": str(ledger_context["currency_id"]),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201

    balance_response = client.get(
        "/api/v1/inventory/balance",
        params={
            "warehouse_id": str(ledger_context["warehouse_id"]),
            "product_id": str(ledger_context["product_id"]),
        },
        headers=auth_headers,
    )
    assert balance_response.status_code == 200
    assert decimal.Decimal(balance_response.json()["balance"]) == decimal.Decimal("15")


@requires_database
def test_api_post_transaction_negative_balance_returns_409(
    client: TestClient,
    auth_headers: dict[str, str],
    ledger_context: dict[str, uuid.UUID],
) -> None:
    response = client.post(
        "/api/v1/inventory/transactions",
        json={
            "product_id": str(ledger_context["product_id"]),
            "warehouse_id": str(ledger_context["warehouse_id"]),
            "movement_type_code": "SALE_OUT",
            "signed_quantity": "-5",
            "unit_cost": "9.5",
            "currency_id": str(ledger_context["currency_id"]),
        },
        headers=auth_headers,
    )
    assert response.status_code == 409


@requires_database
def test_api_post_transaction_without_auth_returns_401(
    client: TestClient, ledger_context: dict[str, uuid.UUID]
) -> None:
    response = client.post(
        "/api/v1/inventory/transactions",
        json={
            "product_id": str(ledger_context["product_id"]),
            "warehouse_id": str(ledger_context["warehouse_id"]),
            "movement_type_code": "RECEIPT_FROM_PRODUCTION",
            "signed_quantity": "1",
            "unit_cost": "1",
            "currency_id": str(ledger_context["currency_id"]),
        },
    )
    assert response.status_code == 401
