"""Tests for safe-delete endpoints: DELETE on products, customers, warehouses,
representatives, product-categories, and units-of-measure.

Skipped automatically if ``DATABASE_URL`` is not configured in the test
environment (same convention as ``test_products.py``).

Each entity has two tests:
- Deleting an unreferenced record → 204 No Content
- Attempting to delete a referenced record → 409 Conflict
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB safe-delete tests",
)

PRODUCT_MANAGE = "PRODUCT_MANAGE"
CUSTOMER_MANAGE = "CUSTOMER_MANAGE"
WAREHOUSE_MANAGE = "WAREHOUSE_MANAGE"
REPRESENTATIVE_MANAGE = "REPRESENTATIVE_MANAGE"


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Create a fresh ``ACTIVE`` ``AppUser`` with all manage permissions,
    log in, and return auth headers.
    """

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_safedel_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"SAFE_DEL_MANAGER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Safe Del Manager (test)", created_by=system_user.id
        )
        for perm_code in [PRODUCT_MANAGE, CUSTOMER_MANAGE, WAREHOUSE_MANAGE, REPRESENTATIVE_MANAGE]:
            try:
                rbac_service.create_permission(
                    session,
                    code=perm_code,
                    name=f"Manage {perm_code}",
                    resource=perm_code.split("_")[0].lower(),
                    action="manage",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=perm_code
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
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


@pytest.fixture()
def default_uom_id() -> str:
    """Return the seeded default UoM's id, creating it if absent."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        session.commit()
        return str(uom.id)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Product DELETE
# ---------------------------------------------------------------------------

@requires_database
def test_delete_unreferenced_product_returns_204(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Deleting a product that nothing references should return 204."""
    sku = f"DEL-{uuid.uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/products",
        json={"sku": sku, "name": "Delete Me", "base_uom_id": default_uom_id},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/v1/products/{product_id}", headers=auth_headers)
    assert del_resp.status_code == 204

    # Confirm it's gone.
    get_resp = client.get(f"/api/v1/products/{sku}", headers=auth_headers)
    assert get_resp.status_code == 404


@requires_database
def test_delete_referenced_product_returns_409(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Deleting a product that is referenced by an order line should return 409."""
    from database.models.customer import Customer
    from database.models.currency import Currency
    from database.models.representative import Representative
    from database.models.order import Order
    from database.models.order_line import OrderLine
    from database.models.price_list import PriceList
    from database.models.price_history import PriceHistory

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)

        # Create product
        sku = f"DELREF-{uuid.uuid4().hex[:8]}"
        product = bootstrap_service.ensure_default_product(session, sku=sku, actor_id=system_user.id)

        # Ensure currency
        currency = bootstrap_service.ensure_base_currency(session, actor_id=system_user.id)

        # Create price list
        pl = PriceList(
            name=f"PL-{uuid.uuid4().hex[:6]}",
            currency_id=currency.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(pl)
        session.flush()

        # Create customer
        cust = Customer(
            code=f"CDEL-{uuid.uuid4().hex[:6]}",
            name="Del Test Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(cust)
        session.flush()

        # Create representative
        rep = Representative(
            code=f"RDEL-{uuid.uuid4().hex[:6]}",
            person_name="Del Test Rep",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(rep)
        session.flush()

        # Create price history for the order line
        ph = PriceHistory(
            product_id=product.id,
            price_list_id=pl.id,
            unit_price=100,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(ph)
        session.flush()

        # Create order + order line referencing the product
        order = Order(
            order_number=f"ORD-DEL-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            representative_id=rep.id,
            sales_channel="OFFICE",
            fulfillment_mode="REP_LOCAL",
            order_type="LOCAL",
            state="DRAFT",
            currency_id=currency.id,
            price_list_id=pl.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(order)
        session.flush()

        ol = OrderLine(
            order_id=order.id,
            product_id=product.id,
            fulfillment_warehouse_id=bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id).id,
            qty_ordered=10,
            unit_price=100,
            line_total=1000,
            fulfillment_mode="REP_LOCAL",
            price_history_id=ph.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(ol)
        session.commit()
        product_id = str(product.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/products/{product_id}", headers=auth_headers)
    assert del_resp.status_code == 409
    assert "Cannot delete" in del_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Customer DELETE
# ---------------------------------------------------------------------------

@requires_database
def test_delete_unreferenced_customer_returns_204(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a customer that nothing references should return 204."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_base_currency(session, actor_id=system_user.id)
        code = f"CDEL-{uuid.uuid4().hex[:6]}"
        cust = customer_service_helper(session, code=code, currency_id=currency.id, created_by=system_user.id)
        customer_id = str(cust.id)
        session.commit()
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/customers/{customer_id}", headers=auth_headers)
    assert del_resp.status_code == 204


@requires_database
def test_delete_referenced_customer_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a customer that has orders should return 409."""
    from database.models.customer import Customer
    from database.models.representative import Representative
    from database.models.order import Order
    from database.models.price_list import PriceList

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_base_currency(session, actor_id=system_user.id)

        cust = Customer(
            code=f"CREF-{uuid.uuid4().hex[:6]}",
            name="Ref Test Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(cust)
        session.flush()

        rep = Representative(
            code=f"RREF-{uuid.uuid4().hex[:6]}",
            person_name="Ref Test Rep",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(rep)
        session.flush()

        pl = PriceList(
            name=f"PL-{uuid.uuid4().hex[:6]}",
            currency_id=currency.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(pl)
        session.flush()

        order = Order(
            order_number=f"ORD-REF-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            representative_id=rep.id,
            sales_channel="OFFICE",
            fulfillment_mode="REP_LOCAL",
            order_type="LOCAL",
            state="DRAFT",
            currency_id=currency.id,
            price_list_id=pl.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(order)
        session.commit()
        customer_id = str(cust.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/customers/{customer_id}", headers=auth_headers)
    assert del_resp.status_code == 409
    assert "Cannot delete" in del_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Warehouse DELETE
# ---------------------------------------------------------------------------

@requires_database
def test_delete_unreferenced_warehouse_returns_204(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a warehouse that nothing references should return 204."""
    code = f"WHDEL-{uuid.uuid4().hex[:6]}"
    create_resp = client.post(
        "/api/v1/warehouses",
        json={"code": code, "name": "Delete Me WH", "type": "REPRESENTATIVE", "ownership_mode": "OWNED"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    warehouse_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/v1/warehouses/{warehouse_id}", headers=auth_headers)
    assert del_resp.status_code == 204


@requires_database
def test_delete_referenced_warehouse_returns_409(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Deleting a warehouse that has inventory transactions should return 409."""
    from database.models.warehouse import Warehouse
    from database.models.product import Product
    from database.models.inventory_transaction import InventoryTransaction

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)

        wh = Warehouse(
            code=f"WHR-{uuid.uuid4().hex[:6]}",
            name="Ref Test Warehouse",
            type="REPRESENTATIVE",
            ownership_mode="OWNED",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(wh)
        session.flush()

        product = bootstrap_service.ensure_default_product(session, sku=f"DELREF-WH-{uuid.uuid4().hex[:6]}", actor_id=system_user.id)

        inv = InventoryTransaction(
            warehouse_id=wh.id,
            product_id=product.id,
            movement_type="RECEIPT",
            signed_quantity=10,
            unit_cost=100,
            reference_text="test",
            created_by=system_user.id,
        )
        session.add(inv)
        session.commit()
        warehouse_id = str(wh.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/warehouses/{warehouse_id}", headers=auth_headers)
    assert del_resp.status_code == 409
    assert "Cannot delete" in del_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Representative DELETE
# ---------------------------------------------------------------------------

@requires_database
def test_delete_unreferenced_representative_returns_204(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a representative that nothing references should return 204."""
    code = f"RDEL-{uuid.uuid4().hex[:6]}"
    create_resp = client.post(
        "/api/v1/representatives",
        json={"code": code, "person_name": "Delete Me Rep"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    rep_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/v1/representatives/{rep_id}", headers=auth_headers)
    assert del_resp.status_code == 204


@requires_database
def test_delete_referenced_representative_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a representative that has orders should return 409."""
    from database.models.customer import Customer
    from database.models.representative import Representative
    from database.models.order import Order
    from database.models.price_list import PriceList

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_base_currency(session, actor_id=system_user.id)

        rep = Representative(
            code=f"RREF2-{uuid.uuid4().hex[:6]}",
            person_name="Ref Test Rep 2",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(rep)
        session.flush()

        cust = Customer(
            code=f"CREF2-{uuid.uuid4().hex[:6]}",
            name="Ref Test Customer 2",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(cust)
        session.flush()

        pl = PriceList(
            name=f"PL-{uuid.uuid4().hex[:6]}",
            currency_id=currency.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(pl)
        session.flush()

        order = Order(
            order_number=f"ORD-RREF-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            representative_id=rep.id,
            sales_channel="OFFICE",
            fulfillment_mode="REP_LOCAL",
            order_type="LOCAL",
            state="DRAFT",
            currency_id=currency.id,
            price_list_id=pl.id,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(order)
        session.commit()
        rep_id = str(rep.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/representatives/{rep_id}", headers=auth_headers)
    assert del_resp.status_code == 409
    assert "Cannot delete" in del_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Product Category DELETE
# ---------------------------------------------------------------------------

@requires_database
def test_delete_unreferenced_product_category_returns_204(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a category that nothing references should return 204."""
    session = get_session_factory()()
    try:
        from database.models.product_category import ProductCategory

        system_user = bootstrap_service.ensure_system_user(session)
        code = f"CATDEL-{uuid.uuid4().hex[:6]}"
        cat = ProductCategory(
            code=code,
            name="Delete Me Category",
            path=f"/{code}",
            level=0,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(cat)
        session.commit()
        cat_id = str(cat.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/product-categories/{cat_id}", headers=auth_headers)
    assert del_resp.status_code == 204


@requires_database
def test_delete_referenced_product_category_returns_409(
    client: TestClient, auth_headers: dict[str, str], default_uom_id: str
) -> None:
    """Deleting a category that has products should return 409."""
    from database.models.product_category import ProductCategory
    from database.models.product import Product

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        code = f"CATREF-{uuid.uuid4().hex[:6]}"
        cat = ProductCategory(
            code=code,
            name="Ref Test Category",
            path=f"/{code}",
            level=0,
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(cat)
        session.flush()

        product = Product(
            sku=f"CATREF-P-{uuid.uuid4().hex[:6]}",
            name="Category Ref Product",
            base_uom_id=uuid.UUID(default_uom_id),
            category_id=cat.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.commit()
        cat_id = str(cat.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/product-categories/{cat_id}", headers=auth_headers)
    assert del_resp.status_code == 409
    assert "Cannot delete" in del_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Unit of Measure DELETE
# ---------------------------------------------------------------------------

@requires_database
def test_delete_unreferenced_uom_returns_204(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a UoM that nothing references should return 204."""
    from database.models.unit_of_measure import UnitOfMeasure

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        code = f"UDEL{uuid.uuid4().hex[:4].upper()}"
        uom = UnitOfMeasure(
            code=code,
            name="Delete Me UoM",
            class_="DERIVED",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(uom)
        session.commit()
        uom_id = str(uom.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/units-of-measure/{uom_id}", headers=auth_headers)
    assert del_resp.status_code == 204


@requires_database
def test_delete_referenced_uom_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a UoM that is used by products should return 409."""
    from database.models.unit_of_measure import UnitOfMeasure
    from database.models.product import Product

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        code = f"UREF{uuid.uuid4().hex[:4].upper()}"
        uom = UnitOfMeasure(
            code=code,
            name="Ref Test UoM",
            class_="DERIVED",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(uom)
        session.flush()

        product = Product(
            sku=f"UREF-P-{uuid.uuid4().hex[:6]}",
            name="UoM Ref Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.commit()
        uom_id = str(uom.id)
    finally:
        session.close()

    del_resp = client.delete(f"/api/v1/units-of-measure/{uom_id}", headers=auth_headers)
    assert del_resp.status_code == 409
    assert "Cannot delete" in del_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def customer_service_helper(session, *, code, currency_id, created_by):
    """Minimal customer creation helper for tests."""
    from database.models.customer import Customer
    cust = Customer(
        code=code,
        name="Test Customer",
        type="CORPORATE",
        currency_id=currency_id,
        status="ACTIVE",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(cust)
    session.flush()
    return cust
