"""Focused tests for the Order-Line Pricing Integration milestone.

Covers:
1. Pricing resolution: order line receives current Price List price.
2. Pricing resolution: correct Price History version is selected.
3. Persistence: unit price is persisted on the Order Line.
4. Persistence: later Price List changes do not modify an existing Order Line.
5. Calculation: quantity × unit price produces the correct line total.
6. Calculation: quantity update uses the persisted unit price.
7. Failure: no applicable Price List → appropriate error (400).
8. Failure: inactive Price List → appropriate error (409).
9. Failure: no current price for Product → appropriate error (422).
10. Authorization: existing order/representative scope remains enforced.

All tests use real PostgreSQL (no mocks).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping order pricing integration tests",
)

ORDER_MANAGE = "ORDER_MANAGE"


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
        username = f"test_price_int_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"PRICE_INT_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Price Integration Tester (test)",
            created_by=system_user.id,
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session, code=code, name=code, resource="order",
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
    return _user_with_permissions(ORDER_MANAGE)


@pytest.fixture()
def pricing_fixtures() -> dict:
    """Create all FK targets for order creation with pricing."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        product = Product(
            sku=f"SKU-PI-{suffix}",
            name="Pricing Integration Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-PI-{suffix}",
            price_type="RETAIL",
            currency_id=currency.id,
            owner_scope="GLOBAL",
            is_active=True,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id,
            price_list_id=price_list.id,
            currency_id=currency.id,
            price_type="RETAIL",
            unit_price=decimal.Decimal("100.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        representative = Representative(
            code=f"REP-PI-{suffix}",
            person_name="Pricing Integration Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-PI-{suffix}",
            name="Pricing Integration Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        # Post stock so reservation can succeed later.
        from services import inventory_service
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
            "product_id": str(product.id),
            "price_history_id": str(price_history.id),
            "price_list_id": str(price_list.id),
            "representative_id": str(representative.id),
            "customer_id": str(customer.id),
        }
    finally:
        session.close()


def _order_payload(
    fx: dict,
    *,
    qty: str = "5",
    price_list_id: str | None = None,
    price_history_id: str | None = None,
    explicit_line_price_history: bool = False,
) -> dict:
    """Build a standard order creation payload."""
    line: dict = {
        "product_id": fx["product_id"],
        "fulfillment_warehouse_id": fx["warehouse_id"],
        "qty_ordered": qty,
        "fulfillment_mode": "REP_LOCAL",
    }
    if explicit_line_price_history:
        line["price_history_id"] = fx["price_history_id"]

    return {
        "customer_id": fx["customer_id"],
        "representative_id": fx["representative_id"],
        "currency_id": fx["currency_id"],
        "price_list_id": price_list_id or fx["price_list_id"],
        "order_type": "LOCAL",
        "fulfillment_mode": "REP_LOCAL",
        "sales_channel": "OFFICE",
        "lines": [line],
    }


# -----------------------------------------------------------------------
# 1. Pricing resolution: order line receives current Price List price
# -----------------------------------------------------------------------

@requires_database
def test_order_line_receives_price_list_price(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """When price_history_id is omitted, the order line auto-resolves
    its price from the order's price list."""
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    line = body["lines"][0]
    assert decimal.Decimal(line["unit_price"]) == decimal.Decimal("100.0000")
    assert decimal.Decimal(line["line_total"]) == decimal.Decimal("500.0000")


# -----------------------------------------------------------------------
# 2. Persistence: unit price is persisted on the Order Line
# -----------------------------------------------------------------------

@requires_database
def test_unit_price_persisted_on_order_line(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """The resolved unit_price and price_history_id are persisted."""
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    body = resp.json()
    line = body["lines"][0]
    # price_history_id should be set (auto-resolved)
    assert line["price_history_id"] == pricing_fixtures["price_history_id"]
    assert decimal.Decimal(line["unit_price"]) == decimal.Decimal("100.0000")


# -----------------------------------------------------------------------
# 3. Persistence: later Price List changes do not modify existing lines
# -----------------------------------------------------------------------

@requires_database
def test_price_list_change_does_not_modify_existing_lines(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """After a price list entry is updated, existing order lines retain
    their historical unit price."""
    # Create an order with the current price (100.0000).
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    order = resp.json()
    order_line = order["lines"][0]
    assert decimal.Decimal(order_line["unit_price"]) == decimal.Decimal("100.0000")

    # Add a new price version to the price list (150.0000).
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        price_list = session.get(PriceList, uuid.UUID(pricing_fixtures["price_list_id"]))
        product = session.get(Product, uuid.UUID(pricing_fixtures["product_id"]))
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

        new_price = PriceHistory(
            product_id=product.id,
            price_list_id=price_list.id,
            currency_id=currency.id,
            price_type="RETAIL",
            unit_price=decimal.Decimal("150.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365),
            created_by=system_user.id,
        )
        session.add(new_price)
        session.flush()

        # Close the previous version.
        old_price = session.get(PriceHistory, uuid.UUID(pricing_fixtures["price_history_id"]))
        old_price.effective_to = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        session.flush()
        session.commit()
    finally:
        session.close()

    # Re-read the order — the line must still show the old price.
    resp2 = client.get(f"/api/v1/orders/{order['id']}", headers=manage_auth)
    assert resp2.status_code == 200
    reread_line = resp2.json()["lines"][0]
    assert decimal.Decimal(reread_line["unit_price"]) == decimal.Decimal("100.0000")
    assert decimal.Decimal(reread_line["line_total"]) == decimal.Decimal("500.0000")


# -----------------------------------------------------------------------
# 4. Calculation: quantity × unit price produces correct line total
# -----------------------------------------------------------------------

@requires_database
def test_line_total_calculation(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """line_total = unit_price × qty_ordered."""
    payload = _order_payload(pricing_fixtures, qty="3")
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    body = resp.json()
    line = body["lines"][0]
    assert decimal.Decimal(line["unit_price"]) == decimal.Decimal("100.0000")
    assert decimal.Decimal(line["line_total"]) == decimal.Decimal("300.0000")
    # grand_total = subtotal - discount_total = 300 - 0
    assert decimal.Decimal(body["grand_total"]) == decimal.Decimal("300.0000")


# -----------------------------------------------------------------------
# 5. Explicit price_history_id override (existing mechanism)
# -----------------------------------------------------------------------

@requires_database
def test_explicit_price_history_id_override(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """When price_history_id is provided on the line, it is used instead
    of auto-resolution."""
    payload = _order_payload(pricing_fixtures, explicit_line_price_history=True)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    body = resp.json()
    line = body["lines"][0]
    assert line["price_history_id"] == pricing_fixtures["price_history_id"]
    assert decimal.Decimal(line["unit_price"]) == decimal.Decimal("100.0000")


# -----------------------------------------------------------------------
# 6. Failure: no applicable Price List → 400
# -----------------------------------------------------------------------

@requires_database
def test_nonexistent_price_list_returns_400(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """A nonexistent price_list_id must fail with 400."""
    payload = _order_payload(pricing_fixtures, price_list_id=str(uuid.uuid4()))
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 400


# -----------------------------------------------------------------------
# 7. Failure: inactive Price List → 409
# -----------------------------------------------------------------------

@requires_database
def test_inactive_price_list_returns_409(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """An inactive price_list must fail with 409."""
    # Deactivate the price list.
    session = get_session_factory()()
    try:
        pl = session.get(PriceList, uuid.UUID(pricing_fixtures["price_list_id"]))
        pl.is_active = False
        session.commit()
    finally:
        session.close()

    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 409


# -----------------------------------------------------------------------
# 8. Failure: no current price for Product → 422
# -----------------------------------------------------------------------

@requires_database
def test_no_current_price_for_product_returns_422(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """When no currently valid price exists for a product in the price list,
    the order must fail with 422."""
    # Close the only price entry.
    session = get_session_factory()()
    try:
        old_price = session.get(PriceHistory, uuid.UUID(pricing_fixtures["price_history_id"]))
        old_price.effective_to = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)
        session.commit()
    finally:
        session.close()

    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 422


# -----------------------------------------------------------------------
# 9. Authorization: order/representative scope remains enforced
# -----------------------------------------------------------------------

@requires_database
def test_representative_cannot_create_order_for_other_rep(
    client: TestClient, pricing_fixtures: dict,
) -> None:
    """A representative-linked user cannot create an order for a different
    representative."""
    # Create a representative-linked user.
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        rep = session.get(Representative, uuid.UUID(pricing_fixtures["representative_id"]))

        suffix = uuid.uuid4().hex[:8]
        username = f"test_replink_{suffix}"
        password = "correct-horse-battery-staple"
        rep_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
            representative_id=rep.id,
        )
        role_code = f"REPLINK_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="RepLink Tester",
            created_by=system_user.id,
        )
        try:
            rbac_service.create_permission(
                session, code=ORDER_MANAGE, name=ORDER_MANAGE,
                resource="order", action="test",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=ORDER_MANAGE,
        )
        rbac_service.assign_role(
            session, user_id=rep_user.id, role_code=role_code,
            assigned_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    # Log in as the representative-linked user.
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session2 = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session2, username_or_email=username, password=password,
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

    rep_headers = {"Authorization": f"Bearer {token}"}

    # Create a second representative.
    session3 = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session3)
        suffix2 = uuid.uuid4().hex[:8]
        other_rep = Representative(
            code=f"REP-OTHER-{suffix2}",
            person_name="Other Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session3.add(other_rep)
        session3.commit()
        other_rep_id = str(other_rep.id)
    finally:
        session3.close()

    payload = _order_payload(pricing_fixtures)
    payload["representative_id"] = other_rep_id
    resp = client.post("/api/v1/orders", json=payload, headers=rep_headers)
    assert resp.status_code == 403


# -----------------------------------------------------------------------
# 10. price_list_id is exposed in order response
# -----------------------------------------------------------------------

@requires_database
def test_order_response_includes_price_list_id(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """The order response includes the price_list_id."""
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    body = resp.json()
    assert body["price_list_id"] == pricing_fixtures["price_list_id"]


# -----------------------------------------------------------------------
# Backfill verification tests
# -----------------------------------------------------------------------


@requires_database
def test_no_orders_with_null_price_list_id() -> None:
    """After migration, no orders should have NULL price_list_id."""
    session = get_session_factory()()
    try:
        from sqlalchemy import text
        null_count = session.execute(
            text('SELECT COUNT(*) FROM erp."order" WHERE price_list_id IS NULL')
        ).scalar()
        assert null_count == 0, f"{null_count} orders still have NULL price_list_id"
    finally:
        session.close()


@requires_database
def test_backfilled_price_list_id_matches_price_history() -> None:
    """Each order's price_list_id should match the price_list_id of its
    order line's price_history entry."""
    session = get_session_factory()()
    try:
        from sqlalchemy import text
        # Check a sample of orders to verify correctness.
        results = session.execute(
            text(
                'SELECT o.id, o.price_list_id, ph.price_list_id AS expected '
                'FROM erp."order" o '
                'JOIN erp.order_line ol ON ol.order_id = o.id '
                'JOIN erp.price_history ph ON ph.id = ol.price_history_id '
                'LIMIT 50'
            )
        ).fetchall()
        assert len(results) > 0, "No orders found to verify"
        for row in results:
            assert str(row[1]) == str(row[2]), (
                f"Order {row[0]}: price_list_id={row[1]} does not match "
                f"expected={row[2]} from price_history"
            )
    finally:
        session.close()


@requires_database
def test_order_line_prices_unchanged_after_backfill() -> None:
    """The backfill must not modify any order line financial values."""
    session = get_session_factory()()
    try:
        from sqlalchemy import text
        results = session.execute(
            text(
                'SELECT ol.unit_price, ol.line_total, ph.unit_price AS ph_price '
                'FROM erp.order_line ol '
                'JOIN erp.price_history ph ON ph.id = ol.price_history_id '
                'LIMIT 50'
            )
        ).fetchall()
        assert len(results) > 0, "No order lines found to verify"
        for row in results:
            assert str(row[0]) == str(row[2]), (
                f"Order line unit_price={row[0]} does not match "
                f"price_history unit_price={row[2]}"
            )
    finally:
        session.close()


@requires_database
def test_price_list_id_not_null_constraint_enforced() -> None:
    """The price_list_id column must be NOT NULL after migration."""
    session = get_session_factory()()
    try:
        from sqlalchemy import text
        result = session.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'erp' AND table_name = 'order' "
                "AND column_name = 'price_list_id'"
            )
        ).fetchone()
        assert result is not None, "Column price_list_id not found"
        assert result[0] == 'NO', f"Expected NOT NULL, got is_nullable={result[0]}"
    finally:
        session.close()


# -----------------------------------------------------------------------
# DRAFT order line editing tests
# -----------------------------------------------------------------------


@requires_database
def test_add_line_to_draft_order(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """A new line can be added to a DRAFT order."""
    # Create order with one line.
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["id"]
    assert len(order["lines"]) == 1
    assert decimal.Decimal(order["grand_total"]) == decimal.Decimal("500.0000")

    # Create a second product + price for the new line.
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        suffix2 = uuid.uuid4().hex[:8]
        product2 = Product(
            sku=f"SKU-PI2-{suffix2}",
            name="Second Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product2)
        session.flush()

        price_list = session.get(PriceList, uuid.UUID(pricing_fixtures["price_list_id"]))
        price_history2 = PriceHistory(
            product_id=product2.id,
            price_list_id=price_list.id,
            currency_id=currency.id,
            price_type="RETAIL",
            unit_price=decimal.Decimal("200.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history2)
        session.flush()

        product2_id = str(product2.id)
        price_history2_id = str(price_history2.id)
        session.commit()
    finally:
        session.close()

    # Add the second line.
    add_payload = {
        "product_id": product2_id,
        "fulfillment_warehouse_id": pricing_fixtures["warehouse_id"],
        "price_history_id": price_history2_id,
        "qty_ordered": "2",
        "fulfillment_mode": "REP_LOCAL",
    }
    resp2 = client.post(
        f"/api/v1/orders/{order_id}/lines",
        json=add_payload,
        headers=manage_auth,
    )
    assert resp2.status_code == 201, resp2.text
    new_line = resp2.json()
    assert decimal.Decimal(new_line["unit_price"]) == decimal.Decimal("200.0000")
    assert decimal.Decimal(new_line["line_total"]) == decimal.Decimal("400.0000")

    # Re-read order -- totals should be updated.
    resp3 = client.get(f"/api/v1/orders/{order_id}", headers=manage_auth)
    assert resp3.status_code == 200
    updated_order = resp3.json()
    assert len(updated_order["lines"]) == 2
    assert decimal.Decimal(updated_order["subtotal"]) == decimal.Decimal("900.0000")
    assert decimal.Decimal(updated_order["grand_total"]) == decimal.Decimal("900.0000")


@requires_database
def test_remove_line_from_draft_order(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """A line can be removed from a DRAFT order (soft-delete)."""
    payload = _order_payload(pricing_fixtures, qty="3")
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["id"]
    line_id = order["lines"][0]["id"]
    assert decimal.Decimal(order["grand_total"]) == decimal.Decimal("300.0000")

    # Remove the line.
    resp2 = client.delete(
        f"/api/v1/orders/{order_id}/lines/{line_id}",
        headers=manage_auth,
    )
    assert resp2.status_code == 200

    # Re-read order -- totals should be zero, no active lines.
    resp3 = client.get(f"/api/v1/orders/{order_id}", headers=manage_auth)
    assert resp3.status_code == 200
    updated_order = resp3.json()
    assert len(updated_order["lines"]) == 0
    assert decimal.Decimal(updated_order["grand_total"]) == decimal.Decimal("0.0000")


@requires_database
def test_update_qty_on_draft_order_line(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """Updating quantity on a DRAFT order line recalculates line total
    using the frozen unit price."""
    payload = _order_payload(pricing_fixtures, qty="5")
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["id"]
    line_id = order["lines"][0]["id"]
    assert decimal.Decimal(order["grand_total"]) == decimal.Decimal("500.0000")

    # Update quantity to 10.
    resp2 = client.patch(
        f"/api/v1/orders/{order_id}/lines/{line_id}",
        json={"qty_ordered": "10"},
        headers=manage_auth,
    )
    assert resp2.status_code == 200, resp2.text
    updated_line = resp2.json()
    assert decimal.Decimal(updated_line["unit_price"]) == decimal.Decimal("100.0000")
    assert decimal.Decimal(updated_line["line_total"]) == decimal.Decimal("1000.0000")

    # Re-read order -- totals should be updated.
    resp3 = client.get(f"/api/v1/orders/{order_id}", headers=manage_auth)
    assert resp3.status_code == 200
    updated_order = resp3.json()
    assert decimal.Decimal(updated_order["grand_total"]) == decimal.Decimal("1000.0000")


@requires_database
def test_edit_non_draft_order_rejected(
    client: TestClient, manage_auth: dict, approve_auth_headers: dict, pricing_fixtures: dict,
) -> None:
    """Editing a non-DRAFT order is rejected with 409."""
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["id"]
    line_id = order["lines"][0]["id"]

    # Submit and approve the order.
    client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth)
    client.post(f"/api/v1/orders/{order_id}/approve", json={}, headers=approve_auth_headers)

    # Try to add a line -- should fail.
    add_payload = {
        "product_id": pricing_fixtures["product_id"],
        "fulfillment_warehouse_id": pricing_fixtures["warehouse_id"],
        "qty_ordered": "1",
        "fulfillment_mode": "REP_LOCAL",
    }
    resp2 = client.post(
        f"/api/v1/orders/{order_id}/lines",
        json=add_payload,
        headers=manage_auth,
    )
    assert resp2.status_code == 409

    # Try to update qty -- should fail.
    resp3 = client.patch(
        f"/api/v1/orders/{order_id}/lines/{line_id}",
        json={"qty_ordered": "1"},
        headers=manage_auth,
    )
    assert resp3.status_code == 409

    # Try to remove a line -- should fail.
    resp4 = client.delete(
        f"/api/v1/orders/{order_id}/lines/{line_id}",
        headers=manage_auth,
    )
    assert resp4.status_code == 409


@requires_database
def test_duplicate_product_on_add_rejected(
    client: TestClient, manage_auth: dict, pricing_fixtures: dict,
) -> None:
    """Adding a line with a product already on the order is rejected."""
    payload = _order_payload(pricing_fixtures)
    resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["id"]

    # Try to add another line with the same product.
    add_payload = {
        "product_id": pricing_fixtures["product_id"],
        "fulfillment_warehouse_id": pricing_fixtures["warehouse_id"],
        "price_history_id": pricing_fixtures["price_history_id"],
        "qty_ordered": "5",
        "fulfillment_mode": "REP_LOCAL",
    }
    resp2 = client.post(
        f"/api/v1/orders/{order_id}/lines",
        json=add_payload,
        headers=manage_auth,
    )
    assert resp2.status_code == 409


@pytest.fixture()
def approve_auth_headers() -> dict[str, str]:
    """Holds both ORDER_MANAGE and ORDER_APPROVE."""
    return _user_with_permissions(ORDER_MANAGE, "ORDER_APPROVE")
