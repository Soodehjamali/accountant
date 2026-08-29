"""Tests for M-09: Commission calculation deduplication.

Verifies that:
1. Authorized representative can calculate commission for own order.
2. Unauthorized/out-of-scope representative is rejected.
3. First commission calculation succeeds.
4. Sequential duplicate calculation is rejected (409).
5. Duplicate request produces no additional commission record.
6. Duplicate request produces no duplicate financial side effect.
7. Two concurrent commission requests cannot create duplicate effects.
8. Admin behavior remains correct.
9. Existing commission behavior/regression remains intact.

All tests use real PostgreSQL.
"""

from __future__ import annotations

import datetime
import decimal
import os
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping commission dedup tests",
)

COMMISSION_MANAGE = "COMMISSION_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"comdedup_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_COMDEUP_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ComDedup {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=COMMISSION_MANAGE, name=COMMISSION_MANAGE, resource="commission", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=COMMISSION_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"comdedup_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_COMDEUP_ADM_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"ComDedupAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=COMMISSION_MANAGE, name=COMMISSION_MANAGE, resource="commission", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=COMMISSION_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_completed_order(session, system_user, rep, customer, currency, warehouse,
                            product, price_history):
    from services import invoice_service, order_service
    from database.models.price_list import PriceList

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

    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order.id, created_by=system_user.id,
    )
    invoice_service.issue_invoice(session, invoice.id, actor_user_id=system_user.id)
    invoice_service.record_payment(
        session, invoice.id, amount=decimal.Decimal("300.0000"), actor_user_id=system_user.id,
    )
    session.refresh(invoice)
    assert invoice.state == "PAID"

    order_service.mark_paid(session, order.id, actor_user_id=system_user.id)
    # Set state to COMPLETED directly (bypass mark_completed's auto-commission
    # calculation) so the manual commission endpoint can be tested.
    from database.models.order import Order as OrderModel
    order = session.get(OrderModel, order.id)
    order.state = "COMPLETED"
    order.updated_by = system_user.id
    session.flush()
    session.refresh(order)
    assert order.state == "COMPLETED"
    return order


def _setup(client: TestClient):
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
            code=f"REPA-CD-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-CD-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-CD-{suffix}", name="CommissionDedup Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-CD-{suffix}", price_type="RETAIL", currency_id=currency.id,
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
            code=f"CUSTA-CD-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-CD-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        order_a = _create_completed_order(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        order_b = _create_completed_order(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )

        headers_a, user_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        headers_b, user_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

    # Create commission config via admin
    resp = client.post(
        "/api/v1/commission-configs",
        json={
            "rate": "10.0000",
            "effective_from": "2025-01-01T00:00:00Z",
            "order_type": "LOCAL",
        },
        headers=headers_admin,
    )
    assert resp.status_code == 201, resp.text

    return {
        "headers_a": headers_a,
        "headers_b": headers_b,
        "headers_admin": headers_admin,
        "order_a_id": str(order_a.id),
        "order_b_id": str(order_b.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestCommissionDedup:
    """Commission calculation deduplication."""

    def test_representative_can_commission_own_order(self, client: TestClient):
        """Scenario 1: Authorized representative can calculate commission for own order."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        txn = resp.json()
        assert txn["signed_amount"] == "30.0000"  # 10% of 300
        assert txn["rate_applied"] == "10.0000"

    def test_representative_cannot_commission_other_rep_order(self, client: TestClient):
        """Scenario 2: Unauthorized/out-of-scope representative is rejected."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_b_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_commission_any_order(self, client: TestClient):
        """Scenario 8: Admin behavior remains correct."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_b_id']}/commission",
            json={},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 201, resp.text
        txn = resp.json()
        assert txn["signed_amount"] == "30.0000"

    def test_first_calculation_succeeds(self, client: TestClient):
        """Scenario 3: First commission calculation succeeds."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text

    def test_sequential_duplicate_rejected(self, client: TestClient):
        """Scenario 4+5: Sequential duplicate calculation is rejected (409),
        produces no additional commission record."""
        data = _setup(client)

        # First calculation: succeeds.
        resp1 = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp1.status_code == 201, resp1.text
        txn1 = resp1.json()

        # Second calculation: must be rejected (409 CONFLICT).
        resp2 = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp2.status_code == 409, (
            f"Expected 409 for duplicate commission, got {resp2.status_code}: {resp2.text}"
        )

        # Verify: exactly one commission record, no duplicate financial effect.
        session = get_session_factory()()
        try:
            count = session.execute(
                select(func.count(CommissionTransaction.id)).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_a_id"])
                )
            ).scalar()
            assert count == 1, f"Expected 1 commission record, got {count}"

            total = session.execute(
                select(func.coalesce(func.sum(CommissionTransaction.signed_amount), 0)).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_a_id"])
                )
            ).scalar()
            assert total == decimal.Decimal("30.0000"), (
                f"Expected total 30.0000, got {total}"
            )
        finally:
            session.close()

    def test_no_duplicate_financial_side_effect(self, client: TestClient):
        """Scenario 6: Duplicate request produces no duplicate financial side effect."""
        data = _setup(client)

        # First calculation.
        resp1 = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp1.status_code == 201

        # Second calculation (duplicate).
        resp2 = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp2.status_code == 409

        # Verify: total commission amount unchanged.
        session = get_session_factory()()
        try:
            total = session.execute(
                select(func.coalesce(func.sum(CommissionTransaction.signed_amount), 0)).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_a_id"])
                )
            ).scalar()
            assert total == decimal.Decimal("30.0000"), (
                f"Expected total 30.0000 after duplicate, got {total}"
            )
        finally:
            session.close()

    def test_concurrent_commission_cannot_duplicate(self, client: TestClient):
        """Scenario 7: Two concurrent commission requests cannot create
        duplicate commission effects.

        Uses direct service calls (not TestClient) for true concurrency,
        matching the pattern in test_reservation_concurrency.py.
        """
        from services import commission_service as cs

        data = _setup(client)
        order_id = uuid.UUID(data["order_a_id"])

        # Get the system_user id for actor_user_id.
        session_setup = get_session_factory()()
        try:
            from services.bootstrap_service import ensure_system_user
            su = ensure_system_user(session_setup)
            su_id = su.id
        finally:
            session_setup.close()

        factory = get_session_factory()
        results = {}

        def calc_commission(label):
            s = factory()
            try:
                try:
                    cs.calculate_commission_for_order(
                        s, order_id=order_id, actor_user_id=su_id,
                    )
                    s.commit()
                    results[label] = "SUCCESS"
                except Exception:
                    s.rollback()
                    results[label] = "REJECTED"
            finally:
                s.close()

        t1 = threading.Thread(target=calc_commission, args=("a",))
        t2 = threading.Thread(target=calc_commission, args=("b",))
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        # Exactly one must succeed, one must be rejected.
        succeeded = sum(1 for v in results.values() if v == "SUCCESS")
        rejected = sum(1 for v in results.values() if v == "REJECTED")
        assert succeeded == 1, (
            f"Expected exactly 1 SUCCESS, got {succeeded}. Results: {results}"
        )
        assert rejected == 1, (
            f"Expected exactly 1 REJECTED, got {rejected}. Results: {results}"
        )

        # Verify database state: exactly one commission record.
        session = get_session_factory()()
        try:
            count = session.execute(
                select(func.count(CommissionTransaction.id)).where(
                    CommissionTransaction.order_id == order_id
                )
            ).scalar()
            assert count == 1, (
                f"Expected 1 commission record after concurrent requests, got {count}"
            )

            total = session.execute(
                select(func.coalesce(func.sum(CommissionTransaction.signed_amount), 0)).where(
                    CommissionTransaction.order_id == order_id
                )
            ).scalar()
            assert total == decimal.Decimal("30.0000"), (
                f"Expected total 30.0000, got {total}"
            )
        finally:
            session.close()

    def test_different_orders_independent(self, client: TestClient):
        """Scenario 9: Commission for different orders is independent."""
        data = _setup(client)

        # Commission for order A.
        resp_a = client.post(
            f"/api/v1/orders/{data['order_a_id']}/commission",
            json={},
            headers=data["headers_a"],
        )
        assert resp_a.status_code == 201

        # Commission for order B (different rep, different order).
        resp_b = client.post(
            f"/api/v1/orders/{data['order_b_id']}/commission",
            json={},
            headers=data["headers_b"],
        )
        assert resp_b.status_code == 201

        # Verify: two independent commission records.
        session = get_session_factory()()
        try:
            count_a = session.execute(
                select(func.count(CommissionTransaction.id)).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_a_id"])
                )
            ).scalar()
            count_b = session.execute(
                select(func.count(CommissionTransaction.id)).where(
                    CommissionTransaction.order_id == uuid.UUID(data["order_b_id"])
                )
            ).scalar()
            assert count_a == 1
            assert count_b == 1
        finally:
            session.close()
