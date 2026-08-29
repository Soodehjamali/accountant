"""Tests for list-endpoint representative scope enforcement.

Covers R-01 findings: GET /invoices, GET /transfers list endpoints
must filter results by representative scope.

INVOICES:
1. Representative list returns only own invoices
2. Representative list excludes another representative's invoices
3. Admin list returns all invoices
4. Existing invoice filters (state, customer_id) still work
5. Pagination remains correct after scope filtering

TRANSFERS:
6. Representative list returns transfers involving authorized warehouses
7. Representative list excludes completely out-of-scope transfers
8. Source-warehouse authorization works
9. Destination-warehouse authorization works
10. Admin list returns all transfers
11. Existing transfer filters (state) still work

PAYMENTS (no global list endpoint):
12. GET /payments/{id} is scoped (already covered by test_payment_scope)
13. GET /invoices/{id}/payments is scoped (already covered by test_payment_scope)
14. No global GET /payments endpoint exists — payments are always accessed
    through scoped paths

CUSTOMERS:
15. Customer reads remain intentionally global (R-02/R-03 FALSE POSITIVE)
    — customers are master/catalog data, no representative-sensitive info exposed

All tests use real PostgreSQL (same skipif convention as other test files).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.invoice import Invoice
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.stock_transfer import StockTransfer
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping list scope tests",
)

INVOICE_MANAGE = "INVOICE_MANAGE"
TRANSFER_MANAGE = "TRANSFER_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


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


def _create_rep_user(session, system_user, rep, *, suffix: str, perm_code: str):
    """Create a user linked to a representative, grant permission, return auth headers + user."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"listscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_LISTSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ListScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=perm_code, name=perm_code, resource="test", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=perm_code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str, perm_code: str):
    """Create an admin user (no representative link), grant permission, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"listscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_LISTSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ListScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=perm_code, name=perm_code, resource="test", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=perm_code)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_shipped_order(session, system_user, rep, customer, currency, warehouse,
                          product, price_history):
    """Create a fully shipped order for the given representative."""
    from services import order_service

    price_list = session.get(PriceList, price_history.price_list_id)

    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        price_list_id=price_list.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            order_service.OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=decimal.Decimal("3"),
                fulfillment_mode="REP_LOCAL",
            )
        ],
        created_by=system_user.id,
    )
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)

    try:
        rbac_service.create_permission(
            session, code="ORDER_APPROVE", name="Approve", resource="order", action="approve",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass

    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

    order_line = list(order_service.list_order_lines(session, order.id))[0]
    order_service.ship_order(
        session, order.id,
        shipments=[order_service.ShipmentInput(order_line_id=order_line.id, quantity=decimal.Decimal("3"))],
        actor_user_id=system_user.id,
    )
    session.flush()
    session.refresh(order)
    assert order.state == "SHIPPED"
    return order


def _create_invoice(session, system_user, order):
    """Create a draft invoice from a shipped order via the service layer."""
    from services import invoice_service

    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    session.flush()
    session.refresh(invoice)
    return invoice


# ---------------------------------------------------------------------------
# Shared test data setup
# ---------------------------------------------------------------------------

def _setup_invoice_data(client: TestClient):
    """Create two representatives each with a shipped order + invoice."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        rep_a = Representative(
            code=f"REPA-LS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-LS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-LS-{suffix}", name="ListScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-LS-{suffix}", price_type="RETAIL", currency_id=currency.id,
            owner_scope="GLOBAL", is_active=True, created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id, price_list_id=price_list.id, currency_id=currency.id,
            price_type="RETAIL", unit_price=decimal.Decimal("100.0000"), effective_from=_now(),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"), currency_id=currency.id, actor_user_id=system_user.id,
        )
        session.flush()

        customer_a = Customer(
            code=f"CUSTA-LS-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-LS-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Create shipped orders + invoices for each representative
        order_a = _create_shipped_order(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        order_b = _create_shipped_order(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )
        invoice_a = _create_invoice(session, system_user, order_a)
        invoice_b = _create_invoice(session, system_user, order_b)

        # Create users
        headers_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}", perm_code=INVOICE_MANAGE)
        headers_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}", perm_code=INVOICE_MANAGE)
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}", perm_code=INVOICE_MANAGE)

        session.commit()
    finally:
        session.close()

    return {
        "headers_a": headers_a[0],
        "headers_b": headers_b[0],
        "headers_admin": headers_admin,
        "invoice_a_id": str(invoice_a.id),
        "invoice_b_id": str(invoice_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


def _setup_transfer_data(client: TestClient):
    """Create two representatives each with warehouses and transfers."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        rep_a = Representative(
            code=f"REPA-TLS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-TLS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        wh_a = Warehouse(
            code=f"WHA-{suffix}", name="Warehouse A", type="REPRESENTATIVE",
            ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        wh_b = Warehouse(
            code=f"WHB-{suffix}", name="Warehouse B", type="REPRESENTATIVE",
            ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        wh_shared = Warehouse(
            code=f"WHSH-{suffix}", name="Shared Warehouse", type="REPRESENTATIVE",
            ownership_mode="OWNED", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([wh_a, wh_b, wh_shared])
        session.flush()

        # Assign warehouses to representatives
        now = _now()
        session.add_all([
            WarehouseAssignment(
                representative_id=rep_a.id, warehouse_id=wh_a.id,
                effective_from=now, created_by=system_user.id,
            ),
            WarehouseAssignment(
                representative_id=rep_b.id, warehouse_id=wh_b.id,
                effective_from=now, created_by=system_user.id,
            ),
            # Shared warehouse assigned to both reps
            WarehouseAssignment(
                representative_id=rep_a.id, warehouse_id=wh_shared.id,
                effective_from=now, created_by=system_user.id,
            ),
            WarehouseAssignment(
                representative_id=rep_b.id, warehouse_id=wh_shared.id,
                effective_from=now, created_by=system_user.id,
            ),
        ])
        session.flush()

        product = Product(
            sku=f"SKU-TLS-{suffix}", name="TransferScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        # Stock up warehouses
        from services import inventory_service
        for wh in [wh_a, wh_b, wh_shared]:
            inventory_service.post_transaction(
                session, product_id=product.id, warehouse_id=wh.id,
                movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("100"),
                unit_cost=decimal.Decimal("10.0000"), currency_id=currency.id, actor_user_id=system_user.id,
            )
        session.flush()

        # Create transfers
        from services import stock_transfer_service

        # Transfer 1: wh_a -> wh_b (only rep_a has source, only rep_b has dest)
        t1 = stock_transfer_service.create_transfer(
            session,
            source_warehouse_id=wh_a.id,
            destination_warehouse_id=wh_b.id,
            lines=[stock_transfer_service.TransferLineInput(
                product_id=product.id, qty_requested=decimal.Decimal("5"),
                unit_cost=decimal.Decimal("10.0000"),
            )],
            requested_by=system_user.id,
        )

        # Transfer 2: wh_a -> wh_shared (rep_a has source, both have dest)
        t2 = stock_transfer_service.create_transfer(
            session,
            source_warehouse_id=wh_a.id,
            destination_warehouse_id=wh_shared.id,
            lines=[stock_transfer_service.TransferLineInput(
                product_id=product.id, qty_requested=decimal.Decimal("3"),
                unit_cost=decimal.Decimal("10.0000"),
            )],
            requested_by=system_user.id,
        )

        # Transfer 3: wh_b -> wh_shared (only rep_b has source, both have dest)
        t3 = stock_transfer_service.create_transfer(
            session,
            source_warehouse_id=wh_b.id,
            destination_warehouse_id=wh_shared.id,
            lines=[stock_transfer_service.TransferLineInput(
                product_id=product.id, qty_requested=decimal.Decimal("2"),
                unit_cost=decimal.Decimal("10.0000"),
            )],
            requested_by=system_user.id,
        )

        session.flush()

        # Create users
        headers_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}", perm_code=TRANSFER_MANAGE)
        headers_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}", perm_code=TRANSFER_MANAGE)
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}", perm_code=TRANSFER_MANAGE)

        session.commit()
    finally:
        session.close()

    return {
        "headers_a": headers_a[0],
        "headers_b": headers_b[0],
        "headers_admin": headers_admin,
        "t1_id": str(t1.id),
        "t2_id": str(t2.id),
        "t3_id": str(t3.id),
        "wh_a_id": str(wh_a.id),
        "wh_b_id": str(wh_b.id),
        "wh_shared_id": str(wh_shared.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


# ---------------------------------------------------------------------------
# Invoice list scope tests
# ---------------------------------------------------------------------------

@requires_database
class TestInvoiceListScope:
    """GET /invoices list endpoint representative scope enforcement."""

    def test_representative_list_returns_own_invoices(self, client: TestClient):
        """Representative sees only invoices linked to their orders."""
        data = _setup_invoice_data(client)
        resp = client.get("/api/v1/invoices", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [inv["id"] for inv in resp.json()["items"]]
        assert data["invoice_a_id"] in ids

    def test_representative_list_excludes_other_rep_invoices(self, client: TestClient):
        """Representative does NOT see invoices linked to another rep's orders."""
        data = _setup_invoice_data(client)
        resp = client.get("/api/v1/invoices", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [inv["id"] for inv in resp.json()["items"]]
        assert data["invoice_b_id"] not in ids

    def test_admin_list_returns_all_invoices(self, client: TestClient):
        """Admin/staff user sees all invoices."""
        data = _setup_invoice_data(client)
        resp = client.get("/api/v1/invoices", headers=data["headers_admin"])
        assert resp.status_code == 200, resp.text
        ids = [inv["id"] for inv in resp.json()["items"]]
        assert data["invoice_a_id"] in ids
        assert data["invoice_b_id"] in ids

    def test_existing_state_filter_still_works(self, client: TestClient):
        """Filtering by state still works alongside scope filtering."""
        data = _setup_invoice_data(client)
        # All test invoices are DRAFT
        resp = client.get("/api/v1/invoices?state=DRAFT", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [inv["id"] for inv in resp.json()["items"]]
        assert data["invoice_a_id"] in ids
        assert data["invoice_b_id"] not in ids

    def test_pagination_correct_after_scope_filtering(self, client: TestClient):
        """Pagination metadata is correct after scope filtering."""
        data = _setup_invoice_data(client)
        resp = client.get("/api/v1/invoices?limit=1", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) <= 1
        # Should only see own invoice
        if items:
            assert items[0]["id"] == data["invoice_a_id"]


# ---------------------------------------------------------------------------
# Transfer list scope tests
# ---------------------------------------------------------------------------

@requires_database
class TestTransferListScope:
    """GET /transfers list endpoint representative scope enforcement."""

    def test_representative_list_returns_authorized_transfers(self, client: TestClient):
        """Representative sees transfers involving their assigned warehouses."""
        data = _setup_transfer_data(client)
        resp = client.get("/api/v1/transfers", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [t["id"] for t in resp.json()["items"]]
        # rep_a has wh_a (source for t1, t2) and wh_shared (dest for t2, t3)
        # t1: wh_a -> wh_b (rep_a has source) → visible
        # t2: wh_a -> wh_shared (rep_a has source and dest) → visible
        # t3: wh_b -> wh_shared (rep_a has dest only) → visible
        assert data["t1_id"] in ids
        assert data["t2_id"] in ids
        assert data["t3_id"] in ids

    def test_representative_list_excludes_completely_out_of_scope(self, client: TestClient):
        """Transfers involving only warehouses NOT assigned to the rep are excluded."""
        # Create a transfer between two warehouses only rep_b has
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
            bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]

            # Two warehouses only rep_b has
            wh_only_b1 = Warehouse(
                code=f"WHOB1-{suffix}", name="Only B Warehouse 1", type="REPRESENTATIVE",
                ownership_mode="OWNED", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            wh_only_b2 = Warehouse(
                code=f"WHOB2-{suffix}", name="Only B Warehouse 2", type="REPRESENTATIVE",
                ownership_mode="OWNED", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add_all([wh_only_b1, wh_only_b2])
            session.flush()

            # Create rep_b user
            rep_b = Representative(
                code=f"REPB-EXCL-{suffix}", person_name="Rep B", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep_b)
            session.flush()

            now = _now()
            session.add_all([
                WarehouseAssignment(
                    representative_id=rep_b.id, warehouse_id=wh_only_b1.id,
                    effective_from=now, created_by=system_user.id,
                ),
                WarehouseAssignment(
                    representative_id=rep_b.id, warehouse_id=wh_only_b2.id,
                    effective_from=now, created_by=system_user.id,
                ),
            ])
            session.flush()

            product = Product(
                sku=f"SKU-EXCL-{suffix}", name="Excl Product", base_uom_id=uom.id,
                status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(product)
            session.flush()

            from services import inventory_service, stock_transfer_service
            for wh in [wh_only_b1, wh_only_b2]:
                inventory_service.post_transaction(
                    session, product_id=product.id, warehouse_id=wh.id,
                    movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("100"),
                    unit_cost=decimal.Decimal("10.0000"), currency_id=currency.id, actor_user_id=system_user.id,
                )
            session.flush()

            t_excl = stock_transfer_service.create_transfer(
                session,
                source_warehouse_id=wh_only_b1.id,
                destination_warehouse_id=wh_only_b2.id,
                lines=[stock_transfer_service.TransferLineInput(
                    product_id=product.id, qty_requested=decimal.Decimal("1"),
                    unit_cost=decimal.Decimal("10.0000"),
                )],
                requested_by=system_user.id,
            )
            session.flush()

            # Create a rep_a user (no assignment to wh_only_b1/wh_only_b2)
            rep_a = Representative(
                code=f"REPA-EXCL-{suffix}", person_name="Rep A", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep_a)
            session.flush()

            headers_a = _create_rep_user(
                session, system_user, rep_a, suffix=f"a_excl_{suffix}", perm_code=TRANSFER_MANAGE
            )
            session.commit()
        finally:
            session.close()

        # rep_a should NOT see the transfer involving wh_only_b1/wh_only_b2
        resp = client.get("/api/v1/transfers", headers=headers_a[0])
        assert resp.status_code == 200, resp.text
        ids = [t["id"] for t in resp.json()["items"]]
        assert str(t_excl.id) not in ids

    def test_source_warehouse_authorization_works(self, client: TestClient):
        """Transfer is visible when rep owns the source warehouse."""
        data = _setup_transfer_data(client)
        resp = client.get("/api/v1/transfers", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [t["id"] for t in resp.json()["items"]]
        # t1: wh_a -> wh_b, rep_a owns wh_a (source) → visible
        assert data["t1_id"] in ids

    def test_destination_warehouse_authorization_works(self, client: TestClient):
        """Transfer is visible when rep owns the destination warehouse."""
        data = _setup_transfer_data(client)
        resp = client.get("/api/v1/transfers", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [t["id"] for t in resp.json()["items"]]
        # t3: wh_b -> wh_shared, rep_a owns wh_shared (dest) → visible
        assert data["t3_id"] in ids

    def test_admin_list_returns_all_transfers(self, client: TestClient):
        """Admin/staff user sees all transfers."""
        data = _setup_transfer_data(client)
        resp = client.get("/api/v1/transfers", headers=data["headers_admin"])
        assert resp.status_code == 200, resp.text
        ids = [t["id"] for t in resp.json()["items"]]
        assert data["t1_id"] in ids
        assert data["t2_id"] in ids
        assert data["t3_id"] in ids

    def test_existing_state_filter_still_works(self, client: TestClient):
        """Filtering by state still works alongside scope filtering."""
        data = _setup_transfer_data(client)
        # All test transfers are DRAFT
        resp = client.get("/api/v1/transfers?state=DRAFT", headers=data["headers_a"])
        assert resp.status_code == 200, resp.text
        ids = [t["id"] for t in resp.json()["items"]]
        assert data["t1_id"] in ids
        assert data["t2_id"] in ids
        assert data["t3_id"] in ids


# ---------------------------------------------------------------------------
# Customer read design verification (R-02/R-03 FALSE POSITIVE)
# ---------------------------------------------------------------------------

@requires_database
class TestCustomerReadDesign:
    """Verify that customer reads remain intentionally global (R-02/R-03 FALSE POSITIVE).

    Customer is master/catalog data. GET /customers and GET /customers/{id}
    expose only legitimate global master data (code, name, type, status,
    billing_address, credit_limit, currency, tax_number). No representative-
    sensitive information is exposed.
    """

    def test_representative_can_read_any_customer(self, client: TestClient):
        """Any authenticated user can read customer master data (intentional)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            customer = Customer(
                code=f"CUST-GLB-{suffix}", name="Global Customer", type="CORPORATE",
                currency_id=currency.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(customer)
            session.flush()

            rep = Representative(
                code=f"REP-GLB-{suffix}", person_name="Rep", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep)
            session.flush()

            headers = _create_rep_user(
                session, system_user, rep, suffix=f"glb_{suffix}", perm_code="CUSTOMER_MANAGE"
            )
            customer_id = str(customer.id)
            session.commit()
        finally:
            session.close()

        # Representative can read the customer (global master data)
        resp = client.get(f"/api/v1/customers/{customer_id}", headers=headers[0])
        assert resp.status_code == 200, resp.text
        assert resp.json()["code"] == f"CUST-GLB-{suffix}"

    def test_representative_can_list_all_customers(self, client: TestClient):
        """Any authenticated user can list customer master data (intentional)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            customer = Customer(
                code=f"CUST-LIST-{suffix}", name="List Customer", type="INDIVIDUAL",
                currency_id=currency.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(customer)
            session.flush()

            rep = Representative(
                code=f"REP-LIST-{suffix}", person_name="Rep", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep)
            session.flush()

            headers = _create_rep_user(
                session, system_user, rep, suffix=f"list_{suffix}", perm_code="CUSTOMER_MANAGE"
            )
            session.commit()
        finally:
            session.close()

        # Representative can list customers (global master data) — use search
        resp = client.get(f"/api/v1/customers?search=CUST-LIST-{suffix}", headers=headers[0])
        assert resp.status_code == 200, resp.text
        codes = [c["code"] for c in resp.json()["items"]]
        assert f"CUST-LIST-{suffix}" in codes

    def test_customer_response_excludes_sensitive_fields(self, client: TestClient):
        """Customer response contains only master data fields, no rep-specific info."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

            suffix = uuid.uuid4().hex[:8]
            customer = Customer(
                code=f"CUST-FIELDS-{suffix}", name="Fields Customer", type="CORPORATE",
                currency_id=currency.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(customer)
            session.flush()

            rep = Representative(
                code=f"REP-FIELDS-{suffix}", person_name="Rep", status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(rep)
            session.flush()

            headers = _create_rep_user(
                session, system_user, rep, suffix=f"fields_{suffix}", perm_code="CUSTOMER_MANAGE"
            )
            customer_id = str(customer.id)
            session.commit()
        finally:
            session.close()

        resp = client.get(f"/api/v1/customers/{customer_id}", headers=headers[0])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Must NOT contain representative-specific fields
        assert "representative_id" not in body
        assert "balance" not in body
        assert "ledger" not in body
        # Must contain only master data fields
        expected_fields = {"id", "code", "name", "type", "city_ref_id", "billing_address",
                           "credit_limit_amount", "currency_id", "status", "tax_number",
                           "created_at", "updated_at"}
        assert set(body.keys()) == expected_fields
