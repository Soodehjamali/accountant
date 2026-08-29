"""Focused tests for customer-price-list assignment and BR-P1 resolution.

Covers:
- CustomerPriceList assignment via API
- resolve_customer_price_list service function
- create_order auto-resolution (price_list_id omitted)
- create_order explicit price_list_id still works
- NoCustomerPriceListError when no assignment exists
- Priority resolution (highest priority wins)
- Time-window validity (effective_from / effective_to)
- Inactive price list rejected
- Nonexistent price list rejected

All tests use real PostgreSQL.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models.customer import Customer
from database.models.customer_price_list import CustomerPriceList
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping customer price list tests",
)

CUSTOMER_MANAGE = "CUSTOMER_MANAGE"
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
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_cpl_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session, username=username,
            email=f"{username}@example.invalid",
            password=password, created_by=system_user.id,
        )

        role_code = f"CPL_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="CPL Tester",
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
    return _user_with_permissions(CUSTOMER_MANAGE, ORDER_MANAGE)


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
            sku=f"SKU-CPL-{suffix}", name="CPL Product",
            base_uom_id=uom.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-CPL-{suffix}", price_type="RETAIL",
            currency_id=currency.id, owner_scope="GLOBAL",
            is_active=True, created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id, price_list_id=price_list.id,
            currency_id=currency.id, price_type="RETAIL",
            unit_price=decimal.Decimal("100.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        representative = Representative(
            code=f"REP-CPL-{suffix}", person_name="CPL Representative",
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-CPL-{suffix}", name="CPL Customer",
            type="CORPORATE", currency_id=currency.id, status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
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


# ===========================================================================
# API tests: Customer Price List Assignment
# ===========================================================================


@requires_database
class TestCustomerPriceListAssignment:
    """API-level tests for customer-price-list management."""

    def test_assign_price_list_to_customer(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """POST /customers/{id}/price-lists should create an assignment."""
        now = datetime.datetime.now(datetime.timezone.utc)
        resp = client.post(
            f"/api/v1/customers/{pricing_fixtures['customer_id']}/price-lists",
            json={
                "price_list_id": pricing_fixtures["price_list_id"],
                "effective_from": now.isoformat(),
                "priority": 1,
            },
            headers=manage_auth,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["price_list_id"] == pricing_fixtures["price_list_id"]
        assert body["customer_id"] == pricing_fixtures["customer_id"]
        assert body["priority"] == 1

    def test_list_customer_price_lists(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """GET /customers/{id}/price-lists should list assignments."""
        now = datetime.datetime.now(datetime.timezone.utc)
        # Create an assignment first.
        client.post(
            f"/api/v1/customers/{pricing_fixtures['customer_id']}/price-lists",
            json={
                "price_list_id": pricing_fixtures["price_list_id"],
                "effective_from": now.isoformat(),
                "priority": 1,
            },
            headers=manage_auth,
        )

        resp = client.get(
            f"/api/v1/customers/{pricing_fixtures['customer_id']}/price-lists",
            headers=manage_auth,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1

    def test_inactive_price_list_rejected(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """Assigning an inactive price list should fail with 409."""
        # Deactivate the price list.
        session = get_session_factory()()
        try:
            pl = session.get(PriceList, uuid.UUID(pricing_fixtures["price_list_id"]))
            pl.is_active = False
            session.commit()
        finally:
            session.close()

        now = datetime.datetime.now(datetime.timezone.utc)
        resp = client.post(
            f"/api/v1/customers/{pricing_fixtures['customer_id']}/price-lists",
            json={
                "price_list_id": pricing_fixtures["price_list_id"],
                "effective_from": now.isoformat(),
                "priority": 1,
            },
            headers=manage_auth,
        )
        assert resp.status_code == 409

    def test_nonexistent_price_list_rejected(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """Assigning a nonexistent price list should fail with 404."""
        now = datetime.datetime.now(datetime.timezone.utc)
        resp = client.post(
            f"/api/v1/customers/{pricing_fixtures['customer_id']}/price-lists",
            json={
                "price_list_id": str(uuid.uuid4()),
                "effective_from": now.isoformat(),
                "priority": 1,
            },
            headers=manage_auth,
        )
        assert resp.status_code == 404


# ===========================================================================
# Service tests: resolve_customer_price_list
# ===========================================================================


@requires_database
class TestResolveCustomerPriceList:
    """Direct tests for the resolution service function."""

    def test_resolves_highest_priority(
        self, pricing_fixtures: dict,
    ):
        """The highest-priority (lowest number) active assignment wins."""
        from services import price_list_service

        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            customer_id = uuid.UUID(pricing_fixtures["customer_id"])
            price_list_id = uuid.UUID(pricing_fixtures["price_list_id"])

            now = datetime.datetime.now(datetime.timezone.utc)

            # Create two assignments: priority 2 and priority 1.
            session.add(CustomerPriceList(
                customer_id=customer_id, price_list_id=price_list_id,
                effective_from=now - datetime.timedelta(days=30),
                priority=2, created_by=system_user.id, updated_by=system_user.id,
            ))
            # Create a second price list for priority 1.
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            suffix = uuid.uuid4().hex[:8]
            pl2 = PriceList(
                name=f"PL-CPL2-{suffix}", price_type="WHOLESALE",
                currency_id=currency.id, owner_scope="GLOBAL",
                is_active=True, created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(pl2)
            session.flush()

            session.add(CustomerPriceList(
                customer_id=customer_id, price_list_id=pl2.id,
                effective_from=now - datetime.timedelta(days=30),
                priority=1, created_by=system_user.id, updated_by=system_user.id,
            ))
            session.flush()

            resolved = price_list_service.resolve_customer_price_list(session, customer_id)
            assert resolved is not None
            assert resolved.id == pl2.id  # priority 1 wins
        finally:
            session.close()

    def test_returns_none_when_no_assignment(
        self, pricing_fixtures: dict,
    ):
        """No assignment → returns None."""
        from services import price_list_service

        session = get_session_factory()()
        try:
            customer_id = uuid.UUID(pricing_fixtures["customer_id"])
            resolved = price_list_service.resolve_customer_price_list(session, customer_id)
            assert resolved is None
        finally:
            session.close()

    def test_expired_assignment_ignored(
        self, pricing_fixtures: dict,
    ):
        """An expired assignment (effective_to in the past) is not resolved."""
        from services import price_list_service

        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            customer_id = uuid.UUID(pricing_fixtures["customer_id"])
            price_list_id = uuid.UUID(pricing_fixtures["price_list_id"])

            now = datetime.datetime.now(datetime.timezone.utc)
            session.add(CustomerPriceList(
                customer_id=customer_id, price_list_id=price_list_id,
                effective_from=now - datetime.timedelta(days=60),
                effective_to=now - datetime.timedelta(days=10),
                priority=1, created_by=system_user.id, updated_by=system_user.id,
            ))
            session.flush()

            resolved = price_list_service.resolve_customer_price_list(session, customer_id)
            assert resolved is None
        finally:
            session.close()


# ===========================================================================
# Integration: create_order auto-resolution
# ===========================================================================


@requires_database
class TestCreateOrderPriceListResolution:
    """Test that create_order resolves price_list from customer assignment
    when price_list_id is omitted."""

    def test_auto_resolves_from_customer_assignment(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """When price_list_id is omitted, the system resolves from
        the customer's price-list assignment."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Assign the price list to the customer.
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            session.add(CustomerPriceList(
                customer_id=uuid.UUID(pricing_fixtures["customer_id"]),
                price_list_id=uuid.UUID(pricing_fixtures["price_list_id"]),
                effective_from=now - datetime.timedelta(days=30),
                priority=1,
                created_by=system_user.id, updated_by=system_user.id,
            ))
            session.commit()
        finally:
            session.close()

        # Create order WITHOUT price_list_id.
        payload = {
            "customer_id": pricing_fixtures["customer_id"],
            "representative_id": pricing_fixtures["representative_id"],
            "currency_id": pricing_fixtures["currency_id"],
            # price_list_id intentionally omitted
            "order_type": "LOCAL",
            "fulfillment_mode": "REP_LOCAL",
            "sales_channel": "OFFICE",
            "lines": [{
                "product_id": pricing_fixtures["product_id"],
                "fulfillment_warehouse_id": pricing_fixtures["warehouse_id"],
                "qty_ordered": "3",
                "fulfillment_mode": "REP_LOCAL",
            }],
        }
        resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["price_list_id"] == pricing_fixtures["price_list_id"]
        # 3 * 100 = 300
        assert decimal.Decimal(body["grand_total"]) == decimal.Decimal("300")

    def test_explicit_price_list_id_still_works(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """When price_list_id is provided explicitly, it is used directly."""
        payload = {
            "customer_id": pricing_fixtures["customer_id"],
            "representative_id": pricing_fixtures["representative_id"],
            "currency_id": pricing_fixtures["currency_id"],
            "price_list_id": pricing_fixtures["price_list_id"],
            "order_type": "LOCAL",
            "fulfillment_mode": "REP_LOCAL",
            "sales_channel": "OFFICE",
            "lines": [{
                "product_id": pricing_fixtures["product_id"],
                "fulfillment_warehouse_id": pricing_fixtures["warehouse_id"],
                "qty_ordered": "2",
                "fulfillment_mode": "REP_LOCAL",
            }],
        }
        resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
        assert resp.status_code == 201
        body = resp.json()
        assert body["price_list_id"] == pricing_fixtures["price_list_id"]

    def test_no_assignment_no_price_list_id_rejected(
        self, client: TestClient, manage_auth: dict, pricing_fixtures: dict,
    ):
        """When no assignment exists and price_list_id is omitted, 422."""
        payload = {
            "customer_id": pricing_fixtures["customer_id"],
            "representative_id": pricing_fixtures["representative_id"],
            "currency_id": pricing_fixtures["currency_id"],
            # price_list_id intentionally omitted
            "order_type": "LOCAL",
            "fulfillment_mode": "REP_LOCAL",
            "sales_channel": "OFFICE",
            "lines": [{
                "product_id": pricing_fixtures["product_id"],
                "fulfillment_warehouse_id": pricing_fixtures["warehouse_id"],
                "qty_ordered": "1",
                "fulfillment_mode": "REP_LOCAL",
            }],
        }
        resp = client.post("/api/v1/orders", json=payload, headers=manage_auth)
        assert resp.status_code == 422
        assert "No price list assigned" in resp.json()["detail"]

    def test_bot_set_price_still_works_with_customer_pricing(
        self, pricing_fixtures: dict,
    ):
        """Bot /set-price still works — it operates on DRAFT order lines,
        independent of how the price list was resolved."""
        from services import order_service

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            rep = session.get(Representative, uuid.UUID(pricing_fixtures["representative_id"]))
            customer = session.get(Customer, uuid.UUID(pricing_fixtures["customer_id"]))
            product = session.get(Product, uuid.UUID(pricing_fixtures["product_id"]))
            price_list = session.get(PriceList, uuid.UUID(pricing_fixtures["price_list_id"]))
            price_history = session.get(PriceHistory, uuid.UUID(pricing_fixtures["price_history_id"]))

            order = order_service.create_order(
                session,
                customer_id=customer.id,
                representative_id=rep.id,
                currency_id=price_list.currency_id,
                price_list_id=price_list.id,
                order_type="LOCAL",
                fulfillment_mode="REP_LOCAL",
                sales_channel="OFFICE",
                lines=[order_service.OrderLineInput(
                    product_id=product.id,
                    fulfillment_warehouse_id=uuid.UUID(pricing_fixtures["warehouse_id"]),
                    price_history_id=price_history.id,
                    qty_ordered=decimal.Decimal("2"),
                    fulfillment_mode="REP_LOCAL",
                )],
                created_by=su.id,
            )

            line = session.execute(
                select(order_service.OrderLine).where(order_service.OrderLine.order_id == order.id)
            ).scalar_one()

            # /set-price still works on DRAFT order lines.
            updated = order_service.update_order_line_price(
                session, order.id, line.id,
                new_unit_price=decimal.Decimal("150"),
                actor_user_id=su.id,
            )
            assert decimal.Decimal(updated.unit_price) == decimal.Decimal("150")
        finally:
            session.close()
