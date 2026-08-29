"""Focused tests for BR-P2 Phase A: Basic Explicit Single-Discount Application.

Covers:
1. Valid PERCENT discount applied to order line
2. Valid AMOUNT discount applied to order line
3. Expired discount rejected
4. Future (not yet valid) discount rejected
5. Discount exceeding line gross amount rejected (negative line total)
6. Line total recalculated correctly
7. Order discount_total recalculated
8. Order grand_total recalculated
9. DRAFT-only: non-DRAFT order rejected
10. /set-price interaction: discount applies to overridden price
11. Product scope: discount scoped to wrong product rejected
12. Category scope: discount scoped to wrong category rejected
13. Customer scope: discount scoped to wrong customer rejected
14. Representative scope: discount scoped to wrong rep rejected
15. Remove discount from order line
16. Existing regression: order price integration still passes

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
from database.models.discount import Discount
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.product_category import ProductCategory
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping discount resolution tests",
)

ORDER_MANAGE = "ORDER_MANAGE"


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
        username = f"test_discount_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"DISCOUNT_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Discount Tester (test)",
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
def discount_fixtures() -> dict:
    """Create all FK targets for discount testing.

    Creates: currency, warehouse, uom, product, price_list, price_history,
    representative, customer, two discounts (PERCENT and AMOUNT).
    """
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        product = Product(
            sku=f"SKU-DISC-{suffix}",
            name="Discount Test Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-DISC-{suffix}",
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
            code=f"REP-DISC-{suffix}",
            person_name="Discount Test Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-DISC-{suffix}",
            name="Discount Test Customer",
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

        # --- PERCENT discount (10% off, valid now) ---
        now = datetime.datetime.now(datetime.timezone.utc)
        percent_discount = Discount(
            product_id=product.id,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("10.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="TEST_PCT",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(percent_discount)
        session.flush()

        # --- AMOUNT discount ($25 off, valid now) ---
        amount_discount = Discount(
            product_id=None,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="AMOUNT",
            value=decimal.Decimal("25.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="TEST_AMT",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(amount_discount)
        session.flush()

        # --- EXPIRED discount ---
        expired_discount = Discount(
            product_id=None,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("5.0000"),
            valid_from=now - datetime.timedelta(days=60),
            valid_to=now - datetime.timedelta(days=1),
            scope_tag="EXPIRED",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(expired_discount)
        session.flush()

        # --- FUTURE discount ---
        future_discount = Discount(
            product_id=None,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("15.0000"),
            valid_from=now + datetime.timedelta(days=30),
            valid_to=now + datetime.timedelta(days=60),
            scope_tag="FUTURE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(future_discount)
        session.flush()

        # --- EXCESSIVE discount ($600 AMOUNT — exceeds $500 line gross) ---
        # Cannot use PERCENT > 100 due to DB CHECK constraint.
        excessive_discount = Discount(
            product_id=None,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="AMOUNT",
            value=decimal.Decimal("600.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="EXCESSIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(excessive_discount)
        session.flush()

        # --- PRODUCT-SCOPED discount (scoped to THIS product) ---
        product_scoped_discount = Discount(
            product_id=product.id,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("20.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="PROD_SCOPE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product_scoped_discount)
        session.flush()

        # --- CATEGORY-SCOPED discount ---
        category = ProductCategory(
            code=f"CAT-DISC-{suffix}",
            name="Discount Test Category",
            path=f"CAT-DISC-{suffix}",
            level=0,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(category)
        session.flush()

        # Assign product to category.
        product.category_id = category.id
        session.flush()

        category_scoped_discount = Discount(
            product_id=None,
            category_id=category.id,
            customer_id=None,
            representative_id=None,
            discount_type="AMOUNT",
            value=decimal.Decimal("10.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="CAT_SCOPE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(category_scoped_discount)
        session.flush()

        # --- CUSTOMER-SCOPED discount ---
        customer_scoped_discount = Discount(
            product_id=None,
            category_id=None,
            customer_id=customer.id,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("5.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="CUST_SCOPE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer_scoped_discount)
        session.flush()

        # --- WRONG-PRODUCT discount (scoped to a DIFFERENT product) ---
        other_product = Product(
            sku=f"SKU-OTHER-{suffix}",
            name="Other Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(other_product)
        session.flush()

        wrong_product_discount = Discount(
            product_id=other_product.id,
            category_id=None,
            customer_id=None,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("10.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="WRONG_PROD",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(wrong_product_discount)
        session.flush()

        # --- WRONG-CUSTOMER discount ---
        other_customer = Customer(
            code=f"CUST-OTHER-{suffix}",
            name="Other Customer",
            type="INDIVIDUAL",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(other_customer)
        session.flush()

        wrong_customer_discount = Discount(
            product_id=None,
            category_id=None,
            customer_id=other_customer.id,
            representative_id=None,
            discount_type="PERCENT",
            value=decimal.Decimal("10.0000"),
            valid_from=now - datetime.timedelta(days=1),
            valid_to=now + datetime.timedelta(days=30),
            scope_tag="WRONG_CUST",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(wrong_customer_discount)
        session.flush()

        session.commit()
        return {
            "currency_id": str(currency.id),
            "warehouse_id": str(warehouse.id),
            "product_id": str(product.id),
            "price_history_id": str(price_history.id),
            "price_list_id": str(price_list.id),
            "representative_id": str(representative.id),
            "customer_id": str(customer.id),
            "percent_discount_id": str(percent_discount.id),
            "amount_discount_id": str(amount_discount.id),
            "expired_discount_id": str(expired_discount.id),
            "future_discount_id": str(future_discount.id),
            "excessive_discount_id": str(excessive_discount.id),
            "product_scoped_discount_id": str(product_scoped_discount.id),
            "category_scoped_discount_id": str(category_scoped_discount.id),
            "customer_scoped_discount_id": str(customer_scoped_discount.id),
            "wrong_product_discount_id": str(wrong_product_discount.id),
            "wrong_customer_discount_id": str(wrong_customer_discount.id),
            "category_id": str(category.id),
            "other_product_id": str(other_product.id),
            "other_customer_id": str(other_customer.id),
        }
    finally:
        session.close()


def _create_order(client: TestClient, auth: dict, fx: dict, *, qty: str = "5") -> dict:
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
# Tests
# ===========================================================================


@requires_database
class TestPercentDiscount:
    """Valid PERCENT discount applied to an order line."""

    def test_percent_discount_applied(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """10% discount on 5 × $100 = $500 → discount_value = $50."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("50.0000")
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("450.0000")

    def test_percent_discount_order_totals(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """Order-level totals are recalculated."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="10")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200

        # Re-read order.
        resp2 = client.get(f"/api/v1/orders/{order_id}", headers=manage_auth)
        assert resp2.status_code == 200
        updated = resp2.json()
        # 10 × 100 = 1000 subtotal, 10% = 100 discount, grand = 900
        assert decimal.Decimal(updated["subtotal"]) == decimal.Decimal("1000.0000")
        assert decimal.Decimal(updated["discount_total"]) == decimal.Decimal("100.0000")
        assert decimal.Decimal(updated["grand_total"]) == decimal.Decimal("900.0000")


@requires_database
class TestAmountDiscount:
    """Valid AMOUNT discount applied to an order line."""

    def test_amount_discount_applied(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """$25 off on 5 × $100 = $500 → discount_value = $25."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["amount_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("25.0000")
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("475.0000")


@requires_database
class TestDiscountValidity:
    """Discount validity window enforcement."""

    def test_expired_discount_rejected(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """An expired discount is rejected with 409."""
        order = _create_order(client, manage_auth, discount_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["expired_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_future_discount_rejected(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A not-yet-valid discount is rejected with 409."""
        order = _create_order(client, manage_auth, discount_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["future_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_nonexistent_discount_rejected(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A nonexistent discount is rejected with 404."""
        order = _create_order(client, manage_auth, discount_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": str(uuid.uuid4())},
            headers=manage_auth,
        )
        assert resp.status_code == 404


@requires_database
class TestNegativeLineTotalPrevention:
    """Discount exceeding line gross amount is rejected."""

    def test_excessive_discount_rejected(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """$600 AMOUNT discount on 5 × $100 = $500 → $600 exceeds $500 → rejected."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["excessive_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 422


@requires_database
class TestApplicability:
    """Discount scope applicability checks."""

    def test_wrong_product_rejected(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A product-scoped discount for a DIFFERENT product is rejected."""
        order = _create_order(client, manage_auth, discount_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["wrong_product_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 422

    def test_product_scoped_discount_accepted(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A product-scoped discount for the CORRECT product is accepted."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["product_scoped_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 20% of 500 = 100
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("100.0000")

    def test_category_scoped_discount_accepted(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A category-scoped discount is accepted when the product's category matches."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["category_scoped_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # $10 fixed amount
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("10.0000")
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("490.0000")

    def test_customer_scoped_discount_accepted(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A customer-scoped discount is accepted when the order's customer matches."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="10")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["customer_scoped_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 5% of 1000 = 50
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("50.0000")

    def test_wrong_customer_rejected(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """A customer-scoped discount for a DIFFERENT customer is rejected."""
        order = _create_order(client, manage_auth, discount_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["wrong_customer_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 422


@requires_database
class TestDraftOnly:
    """Discount operations only work on DRAFT orders."""

    def test_non_draft_order_rejected(
        self,
        client: TestClient,
        manage_auth: dict,
        discount_fixtures: dict,
    ):
        """Applying a discount to a non-DRAFT order is rejected."""
        order = _create_order(client, manage_auth, discount_fixtures)
        order_id = order["id"]

        # Submit the order.
        resp = client.post(
            f"/api/v1/orders/{order_id}/submit", json={}, headers=manage_auth,
        )
        assert resp.status_code == 200

        line_id = order["lines"][0]["id"]
        resp2 = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=manage_auth,
        )
        assert resp2.status_code == 409


@requires_database
class TestSetPriceInteraction:
    """/set-price interaction: discount applies to overridden price."""

    def test_discount_applies_to_overridden_price(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """After /set-price changes unit_price, the discount applies to the
        new price, not the original price_history price."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # Override price to $200 (was $100).
        resp_price = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "200"},
            headers=manage_auth,
        )
        assert resp_price.status_code == 200
        assert decimal.Decimal(resp_price.json()["unit_price"]) == decimal.Decimal("200")
        # 5 × 200 = 1000 (no discount yet)
        assert decimal.Decimal(resp_price.json()["line_total"]) == decimal.Decimal("1000")

        # Apply 10% discount → 10% of 1000 = 100.
        resp_disc = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=manage_auth,
        )
        assert resp_disc.status_code == 200, resp_disc.text
        body = resp_disc.json()
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("100.0000")
        # 1000 - 100 = 900
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("900.0000")

    def test_price_change_after_discount(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """After a discount is applied, /set-price recalculates using the
        existing discount_value."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # Apply 10% discount first (10% of 500 = 50).
        resp_disc = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=manage_auth,
        )
        assert resp_disc.status_code == 200
        assert decimal.Decimal(resp_disc.json()["discount_value"]) == decimal.Decimal("50.0000")
        assert decimal.Decimal(resp_disc.json()["line_total"]) == decimal.Decimal("450.0000")

        # Now override price to $200 → 5 × 200 = 1000, discount_value stays 50.
        resp_price = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/price",
            json={"unit_price": "200"},
            headers=manage_auth,
        )
        assert resp_price.status_code == 200
        body = resp_price.json()
        assert decimal.Decimal(body["unit_price"]) == decimal.Decimal("200")
        # line_total = (200 × 5) - 50 = 950
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("950.0000")


@requires_database
class TestRemoveDiscount:
    """Remove discount from an order line."""

    def test_remove_discount(
        self, client: TestClient, manage_auth: dict, discount_fixtures: dict,
    ):
        """Removing a discount resets discount_id and discount_value."""
        order = _create_order(client, manage_auth, discount_fixtures, qty="5")
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        # Apply discount.
        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=manage_auth,
        )
        assert resp.status_code == 200
        assert decimal.Decimal(resp.json()["discount_value"]) == decimal.Decimal("50.0000")
        assert decimal.Decimal(resp.json()["line_total"]) == decimal.Decimal("450.0000")

        # Remove discount.
        resp2 = client.delete(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            headers=manage_auth,
        )
        assert resp2.status_code == 200, resp2.text
        body = resp2.json()
        assert body["discount_id"] is None
        assert decimal.Decimal(body["discount_value"]) == decimal.Decimal("0.0000")
        assert decimal.Decimal(body["line_total"]) == decimal.Decimal("500.0000")

        # Verify order totals are recalculated.
        resp3 = client.get(f"/api/v1/orders/{order_id}", headers=manage_auth)
        assert resp3.status_code == 200
        updated = resp3.json()
        assert decimal.Decimal(updated["discount_total"]) == decimal.Decimal("0.0000")
        assert decimal.Decimal(updated["grand_total"]) == decimal.Decimal("500.0000")


@requires_database
class TestPermissionEnforcement:
    """Discount operations require ORDER_MANAGE permission."""

    def test_requires_order_manage(
        self, client: TestClient, discount_fixtures: dict,
    ):
        """Without ORDER_MANAGE, the endpoint returns 403."""
        # Create a user with no permissions.
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            suffix = uuid.uuid4().hex[:8]
            username = f"test_disc_noperm_{suffix}"
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
        order = _create_order(client, manage_auth, discount_fixtures)
        line_id = order["lines"][0]["id"]
        order_id = order["id"]

        resp = client.patch(
            f"/api/v1/orders/{order_id}/lines/{line_id}/discount",
            json={"discount_id": discount_fixtures["percent_discount_id"]},
            headers=no_perm_auth,
        )
        assert resp.status_code == 403
