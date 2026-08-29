"""Tests for representative scope service and order authorization (ADR-007).

Covers:
- resolve_representative_customers(): active/future/expired assignments,
  correct representative isolation, multiple customers, deterministic ordering.
- resolve_representative_warehouses(): active/future/expired assignments,
  primary warehouse behavior, correct representative isolation, multiple warehouses.
- get_order_for_representative(): representative can access own order,
  cannot access another representative's order, nonexistent order handled.

All tests use the real PostgreSQL database (no mocks).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from sqlalchemy.orm import Session

from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.representative import Representative
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import bootstrap_service
from services.representative_scope_service import (
    RepresentativeNotFoundError,
    resolve_representative_customers,
    resolve_representative_warehouses,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping representative scope tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _past(days: int = 30) -> datetime.datetime:
    return _now() - datetime.timedelta(days=days)


def _future(days: int = 30) -> datetime.datetime:
    return _now() + datetime.timedelta(days=days)


def _create_representative(session: Session, system_user) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-SCOPE-{suffix.upper()}",
        person_name=f"Scope Test Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_customer(session: Session, system_user, code_suffix: str) -> Customer:
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    customer = Customer(
        code=f"CUST-SCOPE-{code_suffix}",
        name=f"Scope Customer {code_suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()
    return customer


def _create_warehouse(session: Session, system_user, code_suffix: str) -> Warehouse:
    warehouse = Warehouse(
        code=f"WH-SCOPE-{code_suffix}",
        name=f"Scope Warehouse {code_suffix}",
        type="REPRESENTATIVE",
        ownership_mode="OWNED",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(warehouse)
    session.flush()
    return warehouse


def _assign_customer(
    session: Session,
    representative_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    effective_from: datetime.datetime,
    effective_to: datetime.datetime | None = None,
    priority: int = 1,
    actor_id: uuid.UUID,
) -> CustomerRepAssignment:
    assignment = CustomerRepAssignment(
        customer_id=customer_id,
        representative_id=representative_id,
        effective_from=effective_from,
        effective_to=effective_to,
        priority=priority,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()
    return assignment


def _assign_warehouse(
    session: Session,
    representative_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    *,
    is_primary: bool = False,
    effective_from: datetime.datetime,
    effective_to: datetime.datetime | None = None,
    actor_id: uuid.UUID,
) -> WarehouseAssignment:
    assignment = WarehouseAssignment(
        representative_id=representative_id,
        warehouse_id=warehouse_id,
        is_primary=is_primary,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()
    return assignment


# ===========================================================================
# Representative Customer Scope
# ===========================================================================


@requires_database
class TestRepresentativeCustomerScope:
    """resolve_representative_customers() behavior."""

    def test_active_assignment_included(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])

            _assign_customer(
                session, rep.id, customer.id,
                effective_from=_past(10),
                priority=1,
                actor_id=system_user.id,
            )
            session.commit()

            result = resolve_representative_customers(session, rep.id)
            assert len(result) == 1
            assert result[0].id == customer.id
        finally:
            session.close()

    def test_future_assignment_excluded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])

            _assign_customer(
                session, rep.id, customer.id,
                effective_from=_future(10),
                priority=1,
                actor_id=system_user.id,
            )
            session.commit()

            result = resolve_representative_customers(session, rep.id)
            assert len(result) == 0
        finally:
            session.close()

    def test_expired_assignment_excluded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])

            _assign_customer(
                session, rep.id, customer.id,
                effective_from=_past(60),
                effective_to=_past(30),
                priority=1,
                actor_id=system_user.id,
            )
            session.commit()

            result = resolve_representative_customers(session, rep.id)
            assert len(result) == 0
        finally:
            session.close()

    def test_correct_representative_isolation(self):
        """Rep A's customers must not appear in Rep B's results."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep_a = _create_representative(session, system_user)
            rep_b = _create_representative(session, system_user)
            customer_a = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            customer_b = _create_customer(session, system_user, uuid.uuid4().hex[:6])

            _assign_customer(session, rep_a.id, customer_a.id, effective_from=_past(10), priority=1, actor_id=system_user.id)
            _assign_customer(session, rep_b.id, customer_b.id, effective_from=_past(10), priority=1, actor_id=system_user.id)
            session.commit()

            result_a = resolve_representative_customers(session, rep_a.id)
            result_b = resolve_representative_customers(session, rep_b.id)

            assert len(result_a) == 1
            assert result_a[0].id == customer_a.id
            assert len(result_b) == 1
            assert result_b[0].id == customer_b.id
        finally:
            session.close()

    def test_multiple_customers(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            customers = [_create_customer(session, system_user, uuid.uuid4().hex[:6]) for _ in range(3)]

            for i, c in enumerate(customers):
                _assign_customer(session, rep.id, c.id, effective_from=_past(10), priority=i + 1, actor_id=system_user.id)
            session.commit()

            result = resolve_representative_customers(session, rep.id)
            assert len(result) == 3
            result_ids = {c.id for c in result}
            expected_ids = {c.id for c in customers}
            assert result_ids == expected_ids
        finally:
            session.close()

    def test_deterministic_ordering_by_priority(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            sfx = uuid.uuid4().hex[:6]
            c_high = _create_customer(session, system_user, f"H-{sfx}")
            c_med = _create_customer(session, system_user, f"M-{sfx}")
            c_low = _create_customer(session, system_user, f"L-{sfx}")

            _assign_customer(session, rep.id, c_high.id, effective_from=_past(10), priority=1, actor_id=system_user.id)
            _assign_customer(session, rep.id, c_med.id, effective_from=_past(10), priority=2, actor_id=system_user.id)
            _assign_customer(session, rep.id, c_low.id, effective_from=_past(10), priority=3, actor_id=system_user.id)
            session.commit()

            result = resolve_representative_customers(session, rep.id)
            assert len(result) == 3
            assert result[0].id == c_high.id
            assert result[1].id == c_med.id
            assert result[2].id == c_low.id
        finally:
            session.close()

    def test_nonexistent_representative_raises(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            with pytest.raises(RepresentativeNotFoundError):
                resolve_representative_customers(session, uuid.uuid4())
        finally:
            session.close()

    def test_at_parameter_respects_time_window(self):
        """When ``at`` falls within an assignment's window, it is included."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])

            window_start = _past(20)
            window_end = _past(10)
            _assign_customer(
                session, rep.id, customer.id,
                effective_from=window_start,
                effective_to=window_end,
                priority=1,
                actor_id=system_user.id,
            )
            session.commit()

            at_in_window = _past(15)
            result_in = resolve_representative_customers(session, rep.id, at=at_in_window)
            assert len(result_in) == 1

            result_now = resolve_representative_customers(session, rep.id, at=_now())
            assert len(result_now) == 0
        finally:
            session.close()


# ===========================================================================
# Representative Warehouse Scope
# ===========================================================================


@requires_database
class TestRepresentativeWarehouseScope:
    """resolve_representative_warehouses() behavior."""

    def test_active_assignment_included(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            warehouse = _create_warehouse(session, system_user, uuid.uuid4().hex[:6])

            _assign_warehouse(
                session, rep.id, warehouse.id,
                effective_from=_past(10),
                is_primary=False,
                actor_id=system_user.id,
            )
            session.commit()

            result = resolve_representative_warehouses(session, rep.id)
            assert len(result) == 1
            assert result[0].id == warehouse.id
        finally:
            session.close()

    def test_future_assignment_excluded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            warehouse = _create_warehouse(session, system_user, uuid.uuid4().hex[:6])

            _assign_warehouse(
                session, rep.id, warehouse.id,
                effective_from=_future(10),
                is_primary=False,
                actor_id=system_user.id,
            )
            session.commit()

            result = resolve_representative_warehouses(session, rep.id)
            assert len(result) == 0
        finally:
            session.close()

    def test_expired_assignment_excluded(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            warehouse = _create_warehouse(session, system_user, uuid.uuid4().hex[:6])

            _assign_warehouse(
                session, rep.id, warehouse.id,
                effective_from=_past(60),
                effective_to=_past(30),
                is_primary=False,
                actor_id=system_user.id,
            )
            session.commit()

            result = resolve_representative_warehouses(session, rep.id)
            assert len(result) == 0
        finally:
            session.close()

    def test_primary_warehouse_behavior(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            sfx = uuid.uuid4().hex[:6]
            wh_primary = _create_warehouse(session, system_user, f"P-{sfx}")
            wh_secondary = _create_warehouse(session, system_user, f"S-{sfx}")

            _assign_warehouse(session, rep.id, wh_primary.id, is_primary=True, effective_from=_past(10), actor_id=system_user.id)
            _assign_warehouse(session, rep.id, wh_secondary.id, is_primary=False, effective_from=_past(10), actor_id=system_user.id)
            session.commit()

            all_wh = resolve_representative_warehouses(session, rep.id)
            assert len(all_wh) == 2

            primary_wh = resolve_representative_warehouses(session, rep.id, primary_only=True)
            assert len(primary_wh) == 1
            assert primary_wh[0].id == wh_primary.id
        finally:
            session.close()

    def test_correct_representative_isolation(self):
        """Rep A's warehouses must not appear in Rep B's results."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep_a = _create_representative(session, system_user)
            rep_b = _create_representative(session, system_user)
            sfx = uuid.uuid4().hex[:6]
            wh_a = _create_warehouse(session, system_user, f"A-{sfx}")
            wh_b = _create_warehouse(session, system_user, f"B-{sfx}")

            _assign_warehouse(session, rep_a.id, wh_a.id, is_primary=True, effective_from=_past(10), actor_id=system_user.id)
            _assign_warehouse(session, rep_b.id, wh_b.id, is_primary=True, effective_from=_past(10), actor_id=system_user.id)
            session.commit()

            result_a = resolve_representative_warehouses(session, rep_a.id)
            result_b = resolve_representative_warehouses(session, rep_b.id)

            assert len(result_a) == 1
            assert result_a[0].id == wh_a.id
            assert len(result_b) == 1
            assert result_b[0].id == wh_b.id
        finally:
            session.close()

    def test_multiple_warehouses_ordering(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            sfx = uuid.uuid4().hex[:6]
            wh_primary = _create_warehouse(session, system_user, f"P-{sfx}")
            wh_secondary = _create_warehouse(session, system_user, f"S-{sfx}")

            _assign_warehouse(session, rep.id, wh_secondary.id, is_primary=False, effective_from=_past(20), actor_id=system_user.id)
            _assign_warehouse(session, rep.id, wh_primary.id, is_primary=True, effective_from=_past(10), actor_id=system_user.id)
            session.commit()

            result = resolve_representative_warehouses(session, rep.id)
            assert len(result) == 2
            assert result[0].id == wh_primary.id
            assert result[1].id == wh_secondary.id
        finally:
            session.close()

    def test_nonexistent_representative_raises(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            with pytest.raises(RepresentativeNotFoundError):
                resolve_representative_warehouses(session, uuid.uuid4())
        finally:
            session.close()

    def test_primary_only_no_primary_returns_empty(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            wh = _create_warehouse(session, system_user, uuid.uuid4().hex[:6])

            _assign_warehouse(session, rep.id, wh.id, is_primary=False, effective_from=_past(10), actor_id=system_user.id)
            session.commit()

            result = resolve_representative_warehouses(session, rep.id, primary_only=True)
            assert len(result) == 0
        finally:
            session.close()


# ===========================================================================
# Order Authorization (ADR-007 §3)
# ===========================================================================


def _create_order_with_rep(
    session,
    system_user,
    rep,
    customer,
    currency,
    warehouse,
    product,
    price_history,
    inventory_service,
):
    """Helper to create a DRAFT order for authorization tests."""
    from services.order_service import create_order
    from services.order_service import OrderLineInput
    from database.models.price_list import PriceList

    price_list = session.get(PriceList, price_history.price_list_id)

    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal("100"),
        unit_cost=decimal.Decimal("25.0000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()

    order = create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        price_list_id=price_list.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=2,
                fulfillment_mode="REP_LOCAL",
            ),
        ],
        created_by=system_user.id,
    )
    session.flush()
    return order


def _make_order_fixtures(session, system_user):
    """Create all FK targets needed for an order."""
    from database.models.product import Product
    from database.models.price_history import PriceHistory
    from database.models.price_list import PriceList
    from services import inventory_service

    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-SCOPE-{suffix}",
        name="Scope Test Product",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_list = PriceList(
        name=f"Scope PL {suffix}",
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
        unit_price=decimal.Decimal("50.0000"),
        effective_from=_now(),
        created_by=system_user.id,
    )
    session.add(price_history)
    session.flush()

    return currency, warehouse, product, price_history, inventory_service


@requires_database
class TestOrderAuthorization:
    """get_order_for_representative() authorization checks."""

    def test_representative_can_access_own_order(self):
        from services.order_service import get_order_for_representative

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, price_history, inv_svc = _make_order_fixtures(session, system_user)

            order = _create_order_with_rep(
                session, system_user, rep, customer,
                currency, warehouse, product, price_history, inv_svc,
            )

            retrieved = get_order_for_representative(session, order.id, rep.id)
            assert retrieved.id == order.id
        finally:
            session.close()

    def test_representative_cannot_access_other_representatives_order(self):
        from services.order_service import OrderAccessDeniedError, get_order_for_representative

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep_a = _create_representative(session, system_user)
            rep_b = _create_representative(session, system_user)
            customer = _create_customer(session, system_user, uuid.uuid4().hex[:6])
            currency, warehouse, product, price_history, inv_svc = _make_order_fixtures(session, system_user)

            order = _create_order_with_rep(
                session, system_user, rep_a, customer,
                currency, warehouse, product, price_history, inv_svc,
            )

            with pytest.raises(OrderAccessDeniedError):
                get_order_for_representative(session, order.id, rep_b.id)
        finally:
            session.close()

    def test_nonexistent_order_raises_order_not_found(self):
        from services.order_service import OrderNotFoundError, get_order_for_representative

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)

            with pytest.raises(OrderNotFoundError):
                get_order_for_representative(session, uuid.uuid4(), rep.id)
        finally:
            session.close()
