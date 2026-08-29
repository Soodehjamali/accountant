"""Focused API tests for PATCH /orders/{order_id}/lines/{line_id}/price.

Covers:
- Successful price override (unit_price + line_total + order totals)
- Non-DRAFT order rejected (409)
- Invalid/negative price rejected (422)
- Nonexistent order (404)
- Nonexistent order line (422)
- Representative scope isolation (404)
- Permission denial (403)
- price_history_id remains unchanged

All tests use real PostgreSQL via FastAPI TestClient.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping order line price override API tests",
)

ORDER_MANAGE = "ORDER_MANAGE"
ORDER_APPROVE = "ORDER_APPROVE"


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
        username = f"test_price_api_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"PRICE_API_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Price API Tester (test)",
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
def approve_auth() -> dict[str, str]:
    return _user_with_permissions(ORDER_MANAGE, ORDER_APPROVE)


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
            sku=f"SKU-PA-{suffix}",
            name="Price API Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-PA-{suffix}",
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
            code=f"REP-PA-{suffix}",
            person_name="Price API Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-PA-{suffix}",
            name="Price API Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        session.commit()
        return {
            "currency_id": str(currency.id),
            "warehouse_id": str(warehouse.id),
            "product_id": str(product.id),
            "product_sku": product.sku,
            "price_history_id": str(price_history.id),
            "price_list_id": str(price_list.id),
            "representative_id": str(representative.id),
            "customer_id": str(customer.id),
        }
    finally:
        session.close()


def _create_order(client, auth, fx, *, qty="5") -> dict:
    """Helper to create a DRAFT order and return the response body."""
    payload = {
        "customer_id": fx["customer_id"],
        "representative_id": fx["representative_id"],
        "currency_id": fx["currency_id"],
        "price_list_id": fx["price_list_id"],
        "order_type": "LOCAL",
        "fulfillment_mode": "REP_LOCAL",
        "sales_channel": "OFFICE",
        "lines": [
            {
                "product_id": fx["product_id"],
                "fulfillment_warehouse_id": fx["warehouse_id"],
                "price_history_id": fx["price_history_id"],
                "qty_ordered": qty,
                "fulfillment_mode": "REP_LOCAL",
            }
        ],
    }
    resp = client.post("/api/v1/orders", json=payload, headers=auth)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# API tests
# ===========================================================================


@requires_database
class TestPriceOverrideSuccess:
    """Successful price override on a DRAFT order line."""

    def test_overrides_price(self, client: TestClient, manage_auth: dict, pricing_fixtures: dict):
        """PATCH price should update unit_price and line_total."""
        order = _create_order(client, manage_auth, pricing_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "250"},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert decimal.Decimal(body["unit_price"]) == decimal.Decimal("250")
        # 5 * 250 = 1250
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("1250")

    def test_recalculates_order_totals(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """Order grand_total should reflect the new price."""
        order = _create_order(client, manage_auth, pricing_fixtures, qty="3")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # Original: 3 * 100 = 300
        assert decimal.Decimal(order["grand_total"]) == decimal.Decimal("300")

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "50"},
            headers=manage_auth,
        )
        assert resp.status_code == 200

        # Re-read order to verify totals.
        resp2 = client.get(f"/api/v1/orders/{order_id}", headers=manage_auth)
        assert resp2.status_code == 200
        updated_order = resp2.json()
        # 3 * 50 = 150
        assert decimal.Decimal(updated_order["grand_total"]) == decimal.Decimal("150")

    def test_preserves_price_history_id(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """price_history_id must NOT be changed by the price override."""
        order = _create_order(client, manage_auth, pricing_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]
        original_ph_id = order["lines"][0]["price_history_id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "200"},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert resp.json()["price_history_id"] == original_ph_id

    def test_price_zero_allowed(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """A price of 0 should be accepted (non-negative)."""
        order = _create_order(client, manage_auth, pricing_fixtures, qty="2")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "0"},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert decimal.Decimal(resp.json()["unit_price"]) == decimal.Decimal("0")


@requires_database
class TestPriceOverrideValidation:
    """Validation and error cases."""

    def test_non_draft_order_rejected(
        self,
        client: TestClient,
        manage_auth: dict,
        approve_auth: dict,
        pricing_fixtures: dict,
    ):
        """Updating price on a non-DRAFT order is rejected with 409."""
        order = _create_order(client, manage_auth, pricing_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # Submit and approve the order.
        client.post(f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth)
        client.post(f"/api/v1/orders/{order_id}/approve", json={}, headers=approve_auth)

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "200"},
            headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_negative_price_rejected(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """Negative price is rejected with 422."""
        order = _create_order(client, manage_auth, pricing_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "-10"},
            headers=manage_auth,
        )
        assert resp.status_code == 422

    def test_nonexistent_order_rejected(
        self, client: TestClient, manage_auth: dict,
    ):
        """Nonexistent order_id returns 404."""
        fake_order_id = str(uuid.uuid4())
        fake_line_id = str(uuid.uuid4())

        resp = client.patch(
            f"/api/v1/orders/{fake_order_id}/lines/{fake_line_id}/price",
            json={"unit_price": "100"},
            headers=manage_auth,
        )
        assert resp.status_code == 404

    def test_nonexistent_line_rejected(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """Nonexistent line_id returns 422."""
        order = _create_order(client, manage_auth, pricing_fixtures)
        order_id = order["id"]
        fake_line_id = str(uuid.uuid4())

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{fake_line_id}/price",
            json={"unit_price": "100"},
            headers=manage_auth,
        )
        assert resp.status_code == 422

    def test_missing_body_rejected(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """Empty body should be rejected (unit_price required)."""
        order = _create_order(client, manage_auth, pricing_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={},
            headers=manage_auth,
        )
        assert resp.status_code == 422


@requires_database
class TestPriceOverrideScope:
    """Representative scope isolation."""

    def test_rep_cannot_modify_other_rep_order(
        self, client: TestClient, pricing_fixtures: dict):
        """Representative-linked user cannot modify another rep's order."""
        from datetime import timedelta

        from database.models.customer_rep_assignment import CustomerRepAssignment

        # Create a rep-linked user with ORDER_MANAGE + the representative
        # from pricing_fixtures, and assign the customer to the rep.
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            suffix = uuid.uuid4().hex[:8]
            username_a = f"test_price_api_a_{suffix}"
            password = "correct-horse-battery-staple"
            user_a = auth_service.create_user(
                session, username=username_a,
                email=f"{username_a}@example.invalid",
                password=password, created_by=system_user.id,
                representative_id=uuid.UUID(pricing_fixtures["representative_id"]),
            )
            role_code = f"PRICE_API_A_{suffix}"
            rbac_service.create_role(
                session, code=role_code, name="Price API A",
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
                session, user_id=user_a.id, role_code=role_code,
                assigned_by=system_user.id,
            )
            # Assign customer to this rep.
            session.add(CustomerRepAssignment(
                customer_id=uuid.UUID(pricing_fixtures["customer_id"]),
                representative_id=uuid.UUID(pricing_fixtures["representative_id"]),
                effective_from=datetime.datetime.now(datetime.timezone.utc) - timedelta(days=30),
                priority=1,
                created_by=system_user.id,
                updated_by=system_user.id,
            ))
            session.commit()
        finally:
            session.close()

        auth_a = _login(username_a, password)

        # Create order as rep A.
        order = _create_order(client, auth_a, pricing_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # Now create a DIFFERENT rep-linked user (rep B) with manage permission
        # and try to modify rep A's order.
        session2 = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session2)
            system_user2 = bootstrap_service.ensure_system_user(session2)

            suffix2 = uuid.uuid4().hex[:8]
            other_rep = Representative(
                code=f"REP-PA-B-{suffix2}",
                person_name="Rep B",
                status="ACTIVE",
                created_by=system_user2.id,
                updated_by=system_user2.id,
            )
            session2.add(other_rep)
            session2.flush()

            username_b = f"test_price_api_b_{suffix2}"
            user_b = auth_service.create_user(
                session2, username=username_b,
                email=f"{username_b}@example.invalid",
                password="correct-horse-battery-staple",
                created_by=system_user2.id,
                representative_id=other_rep.id,
            )
            role_code_b = f"PRICE_API_B_{suffix2}"
            rbac_service.create_role(
                session2, code=role_code_b, name="Price API B",
                created_by=system_user2.id,
            )
            rbac_service.grant_permission_to_role(
                session2, role_code=role_code_b, permission_code=ORDER_MANAGE,
            )
            rbac_service.assign_role(
                session2, user_id=user_b.id, role_code=role_code_b,
                assigned_by=system_user2.id,
            )
            session2.commit()
        finally:
            session2.close()

        auth_b_rep = _login(username_b, "correct-horse-battery-staple")

        # Rep B (different rep) tries to modify rep A's order.
        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "999"},
            headers=auth_b_rep,
        )
        # Should be 404 (scope denied, not existence leaked).
        assert resp.status_code == 404


@requires_database
class TestPriceOverridePermission:
    """Permission enforcement."""

    def test_requires_order_manage(
        self, client: TestClient, pricing_fixtures: dict,
    ):
        """Without ORDER_MANAGE, the endpoint returns 403."""
        # Create a user with no permissions.
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            suffix = uuid.uuid4().hex[:8]
            username = f"test_price_api_noperm_{suffix}"
            password = "correct-horse-battery-staple"
            auth_service.create_user(
                session, username=username,
                email=f"{username}@example.invalid",
                password=password, created_by=system_user.id,
            )
            session.commit()
        finally:
            session.close()

        no_perm_auth = _login(username, password)

        # Create an order with a user who HAS permission.
        manage_auth = _user_with_permissions(ORDER_MANAGE)
        order = _create_order(client, manage_auth, pricing_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # User without permission tries to update price.
        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "200"},
            headers=no_perm_auth,
        )
        assert resp.status_code == 403
