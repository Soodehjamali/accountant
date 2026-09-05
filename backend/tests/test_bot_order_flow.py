"""Focused tests for the bot representative order-creation endpoints.

Covers the Phase-3 order workflow endpoints added to ``bot.py``:

- ``GET /bot/reps/{rep_id}/customers``        -- ADR-007 scoped list
- ``GET /bot/reps/{rep_id}/products``         -- primary-warehouse inventory
- ``GET /bot/reps/{rep_id}/price-preview``    -- ERP-resolved selling price
- ``POST /bot/reps/{rep_id}/orders``          -- DRAFT order via order_service

Assertions mirror the acceptance list:
1.  representative can list only scoped customers
2.  representative cannot access another representative's customers (IDOR)
3.  representative can list allowed products (own primary warehouse)
4.  price preview resolves the ERP price
5.  missing price returns a controlled error
6.  order creation resolves the price through order_service
7.  caller cannot inject an arbitrary price
8.  multiple order lines work
9.  invalid quantity is rejected
10. authorization failures are rejected
11. created order has the correct representative (from the JWT, never the body)
12. created order has the correct customer
13. created order sales channel is BOT_TELEGRAM
14. order totals come from the ERP calculation

No real Telegram credentials are required -- the tests authenticate through
the real phone-verification endpoint against the test database.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.customer_price_list import CustomerPriceList
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.representative_contact import RepresentativeContact
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from sqlalchemy import select

from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live DB bot tests",
)

BOT_QUERY = "BOT_QUERY"
BOT_WRITE = "BOT_WRITE"


# ---------------------------------------------------------------------------
# Fixture helpers (mirror test_bot_phone_verification.py conventions)
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _unique_phone() -> str:
    return f"+989{uuid.uuid4().int % 10**9:09d}"


def _create_rep(session, system_user, *, phone: str, status: str = "ACTIVE") -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-BOTFLOW-{suffix.upper()}",
        person_name=f"Bot Flow Rep {suffix}",
        status=status,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    contact = RepresentativeContact(
        representative_id=rep.id,
        kind="PHONE",
        value=phone,
        is_primary=True,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(contact)
    session.flush()
    return rep


def _create_user_with_perms(
    session, system_user, *, rep: Representative | None, permissions: list[str],
):
    suffix = uuid.uuid4().hex[:8]
    user = auth_service.create_user(
        session,
        username=f"botflow_user_{suffix}",
        email=f"botflow_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id if rep is not None else None,
    )
    role_code = f"BOTFLOW_ROLE_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"Bot Flow Role {suffix}", created_by=system_user.id
    )
    for code in permissions:
        try:
            rbac_service.create_permission(
                session,
                code=code,
                name=code,
                resource="bot",
                action="test",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=code
        )
    rbac_service.assign_role(
        session, user_id=user.id, role_code=role_code, assigned_by=system_user.id
    )
    session.flush()
    return user


def _assign_warehouse(session, rep_id, warehouse_id, *, is_primary=True, actor_id) -> None:
    session.add(
        WarehouseAssignment(
            representative_id=rep_id,
            warehouse_id=warehouse_id,
            is_primary=is_primary,
            effective_from=_now() - datetime.timedelta(days=30),
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()


def _assign_customer(session, rep_id, customer_id, *, actor_id) -> None:
    session.add(
        CustomerRepAssignment(
            customer_id=customer_id,
            representative_id=rep_id,
            effective_from=_now() - datetime.timedelta(days=30),
            priority=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()


def _assign_price_list_to_customer(session, customer_id, price_list_id, *, actor_id) -> None:
    session.add(
        CustomerPriceList(
            customer_id=customer_id,
            price_list_id=price_list_id,
            effective_from=_now() - datetime.timedelta(days=30),
            priority=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()


def _make_customer(session, system_user, currency, *, name: str = "Flow Customer") -> Customer:
    customer = Customer(
        code=f"CUST-FLOW-{uuid.uuid4().hex[:8].upper()}",
        name=name,
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()
    return customer


def _make_priced_product(
    session, system_user, *, currency, price_list, unit_price: str = "50.0000",
) -> tuple[Product, PriceHistory]:
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-FLOW-{suffix}",
        name=f"Flow Product {suffix}",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_history = PriceHistory(
        product_id=product.id,
        price_list_id=price_list.id,
        currency_id=currency.id,
        price_type=price_list.price_type,
        unit_price=decimal.Decimal(unit_price),
        effective_from=_now() - datetime.timedelta(days=1),
        created_by=system_user.id,
    )
    session.add(price_history)
    session.flush()
    return product, price_history


def _stock_product(session, system_user, *, product_id, warehouse_id, currency, qty: int) -> None:
    inventory_service.post_transaction(
        session,
        product_id=product_id,
        warehouse_id=warehouse_id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal(str(qty)),
        unit_cost=decimal.Decimal("25.0000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()


@pytest.fixture()
def order_flow_ctx():
    """Rep A (BOT_QUERY+BOT_WRITE) with one scoped customer + priced, stocked products."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_bot_platforms(session, system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        # Dedicated warehouse so the product list is isolated from other tests
        # that share the default MAIN warehouse.
        warehouse = Warehouse(
            code=f"WH-FLOW-{uuid.uuid4().hex[:6].upper()}",
            name="Flow Warehouse",
            type="REPRESENTATIVE",
            ownership_mode="OWNED",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(warehouse)
        session.flush()

        phone = _unique_phone()
        rep = _create_rep(session, system_user, phone=phone)
        _create_user_with_perms(
            session, system_user, rep=rep, permissions=[BOT_QUERY, BOT_WRITE]
        )
        _assign_warehouse(session, rep.id, warehouse.id, is_primary=True, actor_id=system_user.id)

        # Customer A assigned to rep A and to a price list.
        customer_a = _make_customer(session, system_user, currency, name="Customer A")
        _assign_customer(session, rep.id, customer_a.id, actor_id=system_user.id)

        # A second customer assigned to rep A but with NO price list.
        customer_no_pl = _make_customer(session, system_user, currency, name="Customer No PL")
        _assign_customer(session, rep.id, customer_no_pl.id, actor_id=system_user.id)

        price_list = PriceList(
            name=f"Flow PL {uuid.uuid4().hex[:8]}",
            price_type="RETAIL",
            currency_id=currency.id,
            owner_scope="GLOBAL",
            is_active=True,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()
        _assign_price_list_to_customer(
            session, customer_a.id, price_list.id, actor_id=system_user.id
        )

        product_a, price_a = _make_priced_product(
            session, system_user, currency=currency, price_list=price_list, unit_price="50000.0000"
        )
        product_b, price_b = _make_priced_product(
            session, system_user, currency=currency, price_list=price_list, unit_price="800000.0000"
        )
        _stock_product(
            session, system_user, product_id=product_a.id, warehouse_id=warehouse.id,
            currency=currency, qty=100,
        )
        _stock_product(
            session, system_user, product_id=product_b.id, warehouse_id=warehouse.id,
            currency=currency, qty=50,
        )

        # A second rep B with the same permissions but no scope of its own.
        phone_b = _unique_phone()
        rep_b = _create_rep(session, system_user, phone=phone_b)
        _create_user_with_perms(
            session, system_user, rep=rep_b, permissions=[BOT_QUERY, BOT_WRITE]
        )

        session.commit()
        yield {
            "session": session,
            "system_user": system_user,
            "rep": rep,
            "rep_b": rep_b,
            "warehouse": warehouse,
            "currency": currency,
            "customer_a": customer_a,
            "customer_no_pl": customer_no_pl,
            "price_list": price_list,
            "product_a": product_a,
            "product_b": product_b,
            "phone_a": phone,
            "phone_b": phone_b,
        }
    finally:
        session.close()


def _verify_phone(client: TestClient, phone: str, chat_id: str) -> str:
    resp = client.post(
        "/api/v1/bot/verify-phone",
        json={"phone_number": phone, "platform": "telegram", "chat_id": chat_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _fresh_order(session, order_id):
    """Load an order row from a fresh session to avoid stale identity maps."""
    from database.models.order import Order

    fresh = get_session_factory()()
    try:
        return fresh.get(Order, order_id)
    finally:
        fresh.close()


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/customers
# ---------------------------------------------------------------------------


@requires_database
class TestBotCustomers:
    def test_rep_lists_only_scoped_customers(self, client: TestClient, order_flow_ctx) -> None:
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-cust-1")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/customers", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        codes = {c["code"] for c in resp.json()["items"]}
        assert order_flow_ctx["customer_a"].code in codes
        assert order_flow_ctx["customer_no_pl"].code in codes
        assert len(codes) == 2  # nothing outside the rep's assignments

    def test_cannot_access_another_reps_customers(self, client: TestClient, order_flow_ctx) -> None:
        """Using rep B's id in the URL with rep A's token must be rejected."""
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-cust-2")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep_b'].id}/customers", headers=_auth(token)
        )
        assert resp.status_code == 403

    def test_requires_auth(self, client: TestClient, order_flow_ctx) -> None:
        resp = client.get(f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/customers")
        assert resp.status_code == 401

    def test_requires_bot_query_permission(self, client: TestClient, order_flow_ctx) -> None:
        """A rep whose linked user has no BOT_QUERY is denied."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        phone = _unique_phone()
        rep_no_perm = _create_rep(session, system_user, phone=phone)
        session.commit()
        token = _verify_phone(client, phone, "tg-cust-3")
        resp = client.get(
            f"/api/v1/bot/reps/{rep_no_perm.id}/customers", headers=_auth(token)
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/products
# ---------------------------------------------------------------------------


@requires_database
class TestBotProducts:
    def test_lists_products_from_primary_warehouse(self, client: TestClient, order_flow_ctx) -> None:
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-prod-1")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/products", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["warehouse_code"] == order_flow_ctx["warehouse"].code
        skus = {p["sku"] for p in body["items"]}
        assert order_flow_ctx["product_a"].sku in skus
        assert order_flow_ctx["product_b"].sku in skus

    def test_rep_without_warehouse_gets_empty_list(self, client: TestClient, order_flow_ctx) -> None:
        """No assigned warehouse -> empty items and warehouse_code == "N/A"."""
        token = _verify_phone(client, order_flow_ctx["phone_b"], "tg-prod-2")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep_b'].id}/products", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["warehouse_code"] == "N/A"

    def test_warehouse_without_stock_returns_empty_items(
        self, client: TestClient, order_flow_ctx
    ) -> None:
        """An assigned warehouse carrying no stock -> empty items but the
        real warehouse code (NOT "N/A"), so the bot shows the
        "محصولی در انبار شما موجود نیست." message."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        empty_warehouse = Warehouse(
            code=f"WH-FLOW-EMPTY-{uuid.uuid4().hex[:6].upper()}",
            name="Empty Flow Warehouse",
            type="REPRESENTATIVE",
            ownership_mode="OWNED",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(empty_warehouse)
        session.flush()
        phone = _unique_phone()
        rep = _create_rep(session, system_user, phone=phone)
        _create_user_with_perms(
            session, system_user, rep=rep, permissions=[BOT_QUERY, BOT_WRITE]
        )
        _assign_warehouse(
            session, rep.id, empty_warehouse.id, is_primary=True, actor_id=system_user.id
        )
        session.commit()

        token = _verify_phone(client, phone, "tg-prod-empty-1")
        resp = client.get(f"/api/v1/bot/reps/{rep.id}/products", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["warehouse_code"] == empty_warehouse.code

    def test_requires_bot_query_permission(self, client: TestClient, order_flow_ctx) -> None:
        """A rep whose linked user has no BOT_QUERY is denied with 403."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        phone = _unique_phone()
        rep_no_perm = _create_rep(session, system_user, phone=phone)
        session.commit()

        token = _verify_phone(client, phone, "tg-prod-3")
        resp = client.get(
            f"/api/v1/bot/reps/{rep_no_perm.id}/products", headers=_auth(token)
        )
        assert resp.status_code == 403

    def test_requires_auth(self, client: TestClient, order_flow_ctx) -> None:
        """No bearer token -> 401 (expired/missing session handling)."""
        resp = client.get(f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/products")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/price-preview
# ---------------------------------------------------------------------------


@requires_database
class TestBotPricePreview:
    def test_resolves_erp_price(self, client: TestClient, order_flow_ctx) -> None:
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-pp-1")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/price-preview",
            params={
                "customer_id": str(order_flow_ctx["customer_a"].id),
                "product_id": str(order_flow_ctx["product_a"].id),
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unit_price"] == 50000.0
        assert body["product_sku"] == order_flow_ctx["product_a"].sku
        assert body["currency_id"] == str(order_flow_ctx["currency"].id)

    def test_customer_outside_scope_rejected(self, client: TestClient, order_flow_ctx) -> None:
        """A customer not assigned to rep A must be rejected even with a valid token."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        foreign_customer = _make_customer(
            session, system_user, order_flow_ctx["currency"], name="Foreign Customer"
        )
        session.commit()
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-pp-2")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/price-preview",
            params={
                "customer_id": str(foreign_customer.id),
                "product_id": str(order_flow_ctx["product_a"].id),
            },
            headers=_auth(token),
        )
        assert resp.status_code == 403

    def test_missing_price_returns_controlled_error(self, client: TestClient, order_flow_ctx) -> None:
        """Product without a current price in the customer's list -> 422, no order."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        product_unpriced, _ = _make_priced_product(
            session,
            system_user,
            currency=order_flow_ctx["currency"],
            price_list=order_flow_ctx["price_list"],
        )
        # Close the price so there is no *currently valid* price.
        from database.models.price_history import PriceHistory

        entry = session.execute(
            select(PriceHistory).where(
                PriceHistory.product_id == product_unpriced.id
            )
        ).scalar_one()
        entry.effective_to = _now() - datetime.timedelta(minutes=1)
        session.commit()

        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-pp-3")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/price-preview",
            params={
                "customer_id": str(order_flow_ctx["customer_a"].id),
                "product_id": str(product_unpriced.id),
            },
            headers=_auth(token),
        )
        assert resp.status_code == 422
        assert "price" in resp.json()["detail"].lower()

    def test_customer_without_price_list_rejected(self, client: TestClient, order_flow_ctx) -> None:
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-pp-4")
        resp = client.get(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/price-preview",
            params={
                "customer_id": str(order_flow_ctx["customer_no_pl"].id),
                "product_id": str(order_flow_ctx["product_a"].id),
            },
            headers=_auth(token),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /bot/reps/{rep_id}/orders
# ---------------------------------------------------------------------------


@requires_database
class TestBotCreateOrder:
    def _create_order(self, client, order_flow_ctx, *, customer_id=None, lines, phone=None):
        token = _verify_phone(client, phone or order_flow_ctx["phone_a"], "tg-ord-1")
        return client.post(
            f"/api/v1/bot/reps/{order_flow_ctx['rep'].id}/orders",
            json={
                "customer_id": str(customer_id or order_flow_ctx["customer_a"].id),
                "order_type": "LOCAL",
                "fulfillment_mode": "REP_LOCAL",
                "lines": lines,
            },
            headers=_auth(token),
        )

    def test_creates_draft_order_with_erp_price(self, client: TestClient, order_flow_ctx) -> None:
        resp = self._create_order(
            client,
            order_flow_ctx,
            lines=[{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 3}],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "DRAFT"
        assert body["order_number"].startswith("ORD-")
        # 3 x 50,000 from the ERP price_history -- not from the caller.
        assert body["grand_total"] == 150000.0
        assert body["lines"][0]["unit_price"] == 50000.0

    def test_order_has_correct_rep_customer_and_channel(self, client: TestClient, order_flow_ctx) -> None:
        resp = self._create_order(
            client,
            order_flow_ctx,
            lines=[{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 1}],
        )
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["order_id"]
        order = _fresh_order(order_flow_ctx["session"], uuid.UUID(order_id))
        assert order is not None
        assert order.representative_id == order_flow_ctx["rep"].id
        assert order.customer_id == order_flow_ctx["customer_a"].id
        assert order.sales_channel == "BOT_TELEGRAM"
        assert order.state == "DRAFT"

    def test_cannot_inject_arbitrary_price(self, client: TestClient, order_flow_ctx) -> None:
        """Extra unit_price fields are ignored; the ERP price wins."""
        resp = self._create_order(
            client,
            order_flow_ctx,
            lines=[
                {
                    "product_id": str(order_flow_ctx["product_a"].id),
                    "qty_ordered": 2,
                    "unit_price": 1.0,  # injected by a malicious caller
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["lines"][0]["unit_price"] == 50000.0  # ERP price, not 1.0
        assert body["grand_total"] == 100000.0

    def test_multiple_lines(self, client: TestClient, order_flow_ctx) -> None:
        resp = self._create_order(
            client,
            order_flow_ctx,
            lines=[
                {"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 10},
                {"product_id": str(order_flow_ctx["product_b"].id), "qty_ordered": 4},
            ],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # (10 x 50,000) + (4 x 800,000) = 3,700,000 -- ERP totals.
        assert body["grand_total"] == 3700000.0
        assert len(body["lines"]) == 2

    def test_invalid_quantity_rejected(self, client: TestClient, order_flow_ctx) -> None:
        for bad_qty in (0, -3):
            resp = self._create_order(
                client,
                order_flow_ctx,
                lines=[{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": bad_qty}],
            )
            assert resp.status_code == 422

    def test_duplicate_product_rejected(self, client: TestClient, order_flow_ctx) -> None:
        resp = self._create_order(
            client,
            order_flow_ctx,
            lines=[
                {"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 1},
                {"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 2},
            ],
        )
        assert resp.status_code == 409

    def test_customer_outside_scope_rejected(self, client: TestClient, order_flow_ctx) -> None:
        """Ordering for a customer not assigned to the rep is forbidden."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        foreign = _make_customer(session, system_user, order_flow_ctx["currency"])
        session.commit()
        resp = self._create_order(
            client,
            order_flow_ctx,
            customer_id=foreign.id,
            lines=[{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 1}],
        )
        assert resp.status_code == 403

    def test_no_price_list_for_customer_returns_422(self, client: TestClient, order_flow_ctx) -> None:
        resp = self._create_order(
            client,
            order_flow_ctx,
            customer_id=order_flow_ctx["customer_no_pl"].id,
            lines=[{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 1}],
        )
        assert resp.status_code == 422

    def test_requires_bot_write(self, client: TestClient, order_flow_ctx) -> None:
        """A rep whose linked user holds only BOT_QUERY cannot create orders."""
        session = order_flow_ctx["session"]
        system_user = order_flow_ctx["system_user"]
        phone = _unique_phone()
        rep_read_only = _create_rep(session, system_user, phone=phone)
        _create_user_with_perms(
            session, system_user, rep=rep_read_only, permissions=[BOT_QUERY]
        )
        _assign_warehouse(
            session, rep_read_only.id, order_flow_ctx["warehouse"].id,
            is_primary=True, actor_id=system_user.id,
        )
        _assign_customer(
            session, rep_read_only.id, order_flow_ctx["customer_a"].id, actor_id=system_user.id
        )
        session.commit()

        token = _verify_phone(client, phone, "tg-ord-2")
        resp = client.post(
            f"/api/v1/bot/reps/{rep_read_only.id}/orders",
            json={
                "customer_id": str(order_flow_ctx["customer_a"].id),
                "order_type": "LOCAL",
                "fulfillment_mode": "REP_LOCAL",
                "lines": [{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 1}],
            },
            headers=_auth(token),
        )
        assert resp.status_code == 403

    def test_rep_id_cannot_be_spoofed(self, client: TestClient, order_flow_ctx) -> None:
        """A rep cannot create an order under another rep's URL id."""
        token = _verify_phone(client, order_flow_ctx["phone_a"], "tg-ord-3")
        resp = client.post(
            f"/api/v1/bot/reps/{order_flow_ctx['rep_b'].id}/orders",
            json={
                "customer_id": str(order_flow_ctx["customer_a"].id),
                "order_type": "LOCAL",
                "fulfillment_mode": "REP_LOCAL",
                "lines": [{"product_id": str(order_flow_ctx["product_a"].id), "qty_ordered": 1}],
            },
            headers=_auth(token),
        )
        assert resp.status_code == 403


__all__: list[str] = []
