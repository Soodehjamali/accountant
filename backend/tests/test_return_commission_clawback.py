"""Focused tests for the Customer Return → Commission Clawback integration.

Covers:
1. Scenario-B (DIRECT) return triggers commission clawback
2. Correct commission transaction is reversed (negative amount)
3. Original commission transaction remains immutable
4. Duplicate processing does not create duplicate clawbacks
5. Non-Scenario-B (LOCAL) return does not trigger clawback
6. Return without originating order does not trigger clawback
7. Return without existing commission does not trigger clawback
8. close_return() state transitions (INSPECTED → CLOSED)
9. close_return() triggers clawback for DIRECT returns
10. close_return() does not trigger clawback for LOCAL returns

All tests use real PostgreSQL (no mocks).
"""

from __future__ import annotations

import decimal
import os
import uuid

import datetime
import pytest
from sqlalchemy import select

from database.models.commission_config import CommissionConfig
from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.customer_return import CustomerReturn
from database.models.order import Order
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.return_line import ReturnLine
from database.session import get_session_factory
from database.models.reason_code_ref import ReasonCodeRef
from services import auth_service, bootstrap_service, inventory_service, order_service, rbac_service
from services.commission_service import get_order_commission
from services.order_service import OrderLineInput, ShipmentInput

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping return commission clawback tests",
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
        username = f"test_ret_claw_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"RET_CLAW_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Return Clawback Tester (test)",
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
    return _user_with_permissions(ORDER_MANAGE, "COMMISSION_MANAGE")


def _create_direct_order_with_commission(session, *, order_type="DIRECT") -> dict:
    """Helper: create an order, advance it to COMPLETED, calculate commission.

    Returns a dict with IDs needed by tests.
    """
    system_user = bootstrap_service.ensure_system_user(session)
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:8]

    representative = Representative(
        code=f"REP-RCB-{suffix}",
        person_name="Return Clawback Representative",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(representative)
    session.flush()

    customer = Customer(
        code=f"CUST-RCB-{suffix}",
        name="Return Clawback Customer",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()

    product = Product(
        sku=f"SKU-RCB-{suffix}",
        name="Return Clawback Product",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_list = PriceList(
        name=f"PL-RCB-{suffix}",
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
        unit_price=decimal.Decimal("200.0000"),
        effective_from=datetime.datetime.now(datetime.timezone.utc),
        created_by=system_user.id,
    )
    session.add(price_history)
    session.flush()

    # Post stock for factory warehouse.
    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal("100"),
        unit_cost=decimal.Decimal("100.0000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )

    # Commission config: 10% for this rep and order type.
    config = CommissionConfig(
        representative_id=representative.id,
        order_type=order_type,
        rate=decimal.Decimal("10.0000"),
        effective_from=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30),
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(config)
    session.flush()

    # Create and advance order to COMPLETED.
    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=representative.id,
        currency_id=currency.id,
        price_list_id=price_list.id,
        order_type=order_type,
        fulfillment_mode="FACTORY_DIRECT" if order_type == "DIRECT" else "REP_LOCAL",
        sales_channel="OFFICE",
        lines=[OrderLineInput(
            product_id=product.id,
            fulfillment_warehouse_id=warehouse.id,
            price_history_id=price_history.id,
            qty_ordered=decimal.Decimal("5"),
            fulfillment_mode="FACTORY_DIRECT" if order_type == "DIRECT" else "REP_LOCAL",
        )],
        created_by=system_user.id,
    )

    # Advance through full lifecycle.
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)
    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

    order_lines = list(order_service.list_order_lines(session, order.id))
    order_service.ship_order(
        session, order.id, actor_user_id=system_user.id,
        shipments=[ShipmentInput(order_line_id=order_lines[0].id, quantity=decimal.Decimal("5"))],
    )
    order_service.mark_invoiced(session, order.id, actor_user_id=system_user.id)
    order_service.mark_paid(session, order.id, actor_user_id=system_user.id)
    order_service.mark_completed(session, order.id, actor_user_id=system_user.id)

    # Calculate commission — may already exist from mark_completed.
    from services.commission_service import calculate_commission_for_order, CommissionAlreadyCalculatedError
    try:
        accrued = calculate_commission_for_order(session, order_id=order.id, actor_user_id=system_user.id)
    except CommissionAlreadyCalculatedError:
        accrued = get_order_commission(session, order.id)
        assert accrued is not None

    session.flush()

    return {
        "order": order,
        "order_id": order.id,
        "customer_id": customer.id,
        "representative_id": representative.id,
        "warehouse_id": warehouse.id,
        "product_id": product.id,
        "order_line_id": order_lines[0].id,
        "accrued_txn": accrued,
        "accrued_txn_id": accrued.id,
        "accrued_amount": accrued.signed_amount,
        "currency_id": currency.id,
    }


def _ensure_reason_code(session) -> uuid.UUID:
    """Ensure a reason_code_ref row exists and return its ID."""
    rc = session.execute(
        select(ReasonCodeRef).where(ReasonCodeRef.code == "DEFECTIVE")
    ).scalar_one_or_none()
    if rc is not None:
        return rc.id
    system_user = bootstrap_service.ensure_system_user(session)
    rc = ReasonCodeRef(
        code="DEFECTIVE",
        scope="RETURN",
        label="Defective Product",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rc)
    session.flush()
    return rc.id


def _get_system_user_id(session) -> uuid.UUID:
    """Get the system user ID for use as actor_user_id."""
    return bootstrap_service.ensure_system_user(session).id


def _create_return(session, order_data: dict, *, state: str = "PENDING_APPROVAL") -> CustomerReturn:
    """Helper: create a CustomerReturn for the given order."""
    system_user = bootstrap_service.ensure_system_user(session)
    reason_code_id = _ensure_reason_code(session)
    suffix = uuid.uuid4().hex[:8]
    cr = CustomerReturn(
        return_number=f"RET-RCB-{suffix}",
        order_id=order_data["order_id"],
        customer_id=order_data["customer_id"],
        representative_id=order_data["representative_id"],
        warehouse_id=order_data["warehouse_id"],
        initiated_by=system_user.id,
        reason_code_id=reason_code_id,
        return_type="CUSTOMER_RETURN",
        state=state,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(cr)
    session.flush()

    # Create a return line.
    rl = ReturnLine(
        customer_return_id=cr.id,
        order_line_id=order_data["order_line_id"],
        product_id=order_data["product_id"],
        qty_returned=decimal.Decimal("2"),
        unit_refund_amount=decimal.Decimal("0"),
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rl)
    session.flush()
    return cr


# ===========================================================================
# Unit Tests: return_service._trigger_commission_clawback
# ===========================================================================


@requires_database
class TestTriggerCommissionClawback:
    """Test the commission clawback trigger in isolation."""

    def test_direct_order_triggers_clawback(self, manage_auth):
        """Scenario-B (DIRECT) return triggers commission clawback."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="DIRECT")
            customer_return = _create_return(session, order_data)

            from services.return_service import _trigger_commission_clawback
            actor_id = _get_system_user_id(session)
            result = _trigger_commission_clawback(
                session, customer_return, actor_id,
            )

            assert result is not None
            assert "clawback" in result.lower()
            assert "negative" in str(result) or "-" in str(result)

            # Verify the CLAWED_BACK transaction was created.
            clawback = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order_data["order_id"],
                    CommissionTransaction.state_event == "CLAWED_BACK",
                )
            ).scalar_one_or_none()
            assert clawback is not None
            assert decimal.Decimal(str(clawback.signed_amount)) < 0
            assert clawback.reversal_of_id == order_data["accrued_txn_id"]
        finally:
            session.close()

    def test_clawback_amount_is_negative(self, manage_auth):
        """Clawback creates a row with negative signed_amount."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="DIRECT")
            customer_return = _create_return(session, order_data)

            from services.return_service import _trigger_commission_clawback
            actor_id = _get_system_user_id(session)
            _trigger_commission_clawback(
                session, customer_return, actor_id,
            )

            clawback = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order_data["order_id"],
                    CommissionTransaction.state_event == "CLAWED_BACK",
                )
            ).scalar_one_or_none()
            assert clawback is not None
            assert clawback.signed_amount < 0
        finally:
            session.close()

    def test_original_commission_remains_immutable(self, manage_auth):
        """Original ACCRUED commission row is not modified by clawback."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="DIRECT")
            original_amount = order_data["accrued_amount"]

            from services.return_service import _trigger_commission_clawback
            customer_return = _create_return(session, order_data)
            actor_id = _get_system_user_id(session)
            _trigger_commission_clawback(
                session, customer_return, actor_id,
            )

            # Reload original transaction — should be unchanged.
            session.expire_all()
            original = session.get(CommissionTransaction, order_data["accrued_txn_id"])
            assert original is not None
            assert original.signed_amount == original_amount
            assert original.state_event == "ACCRUED"
        finally:
            session.close()

    def test_duplicate_clawback_prevented(self, manage_auth):
        """Second clawback attempt for the same order is a no-op."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="DIRECT")
            customer_return = _create_return(session, order_data)

            from services.return_service import _trigger_commission_clawback
            actor_id = _get_system_user_id(session)
            # First call — should clawback.
            result1 = _trigger_commission_clawback(
                session, customer_return, actor_id,
            )
            assert result1 is not None

            # Second call — should detect existing clawback.
            result2 = _trigger_commission_clawback(
                session, customer_return, actor_id,
            )
            assert result2 is not None
            assert "already recorded" in result2.lower()

            # Verify only one CLAWED_BACK row exists.
            clawbacks = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order_data["order_id"],
                    CommissionTransaction.state_event == "CLAWED_BACK",
                )
            ).scalars().all()
            assert len(clawbacks) == 1
        finally:
            session.close()

    def test_local_order_no_clawback(self, manage_auth):
        """Non-Scenario-B (LOCAL) return does NOT trigger clawback."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="LOCAL")
            customer_return = _create_return(session, order_data)

            from services.return_service import _trigger_commission_clawback
            actor_id = _get_system_user_id(session)
            result = _trigger_commission_clawback(
                session, customer_return, actor_id,
            )

            assert result is None  # No clawback for LOCAL orders.

            # Verify no CLAWED_BACK transactions exist.
            clawbacks = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order_data["order_id"],
                    CommissionTransaction.state_event == "CLAWED_BACK",
                )
            ).scalars().all()
            assert len(clawbacks) == 0
        finally:
            session.close()

    def test_no_order_no_clawback(self, manage_auth):
        """Return without originating order does not trigger clawback."""
        session = get_session_factory()()
        try:
            system_user = bootstrap_service.ensure_system_user(session)
            warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
            currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
            reason_code_id = _ensure_reason_code(session)

            # Create a real customer and representative for FK constraints.
            suffix = uuid.uuid4().hex[:8]
            customer = Customer(
                code=f"CUST-NOORD-{suffix}", name="No Order Customer",
                type="CORPORATE", currency_id=currency.id, status="ACTIVE",
                created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(customer)
            session.flush()
            representative = Representative(
                code=f"REP-NOORD-{suffix}", person_name="No Order Rep",
                status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
            )
            session.add(representative)
            session.flush()

            cr = CustomerReturn(
                return_number=f"RET-NOORD-{suffix}",
                order_id=None,  # No originating order
                customer_id=customer.id,
                representative_id=representative.id,
                warehouse_id=warehouse.id,
                initiated_by=system_user.id,
                reason_code_id=reason_code_id,
                return_type="CUSTOMER_RETURN",
                state="PENDING_APPROVAL",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session.add(cr)
            session.flush()

            from services.return_service import _trigger_commission_clawback
            result = _trigger_commission_clawback(session, cr, system_user.id)
            assert result is None
        finally:
            session.close()

    def test_no_commission_no_clawback(self, manage_auth):
        """Return referencing an order with no commission does not trigger clawback."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="DIRECT")

            # Delete the commission transaction to simulate no commission.
            session.execute(
                CommissionTransaction.__table__.delete().where(
                    CommissionTransaction.order_id == order_data["order_id"],
                )
            )
            session.flush()

            customer_return = _create_return(session, order_data)

            from services.return_service import _trigger_commission_clawback
            actor_id = _get_system_user_id(session)
            result = _trigger_commission_clawback(
                session, customer_return, actor_id,
            )
            assert result is None
        finally:
            session.close()


# ===========================================================================
# Unit Tests: return_service state transitions
# ===========================================================================


@requires_database
class TestReturnStateTransitions:
    """Test return_service state machine."""

    def test_close_return_transitions_to_closed(self, manage_auth):
        """close_return() transitions INSPECTED → CLOSED."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="LOCAL")
            customer_return = _create_return(session, order_data, state="INSPECTED")

            from services.return_service import close_return
            actor_id = _get_system_user_id(session)
            closed = close_return(
                session, customer_return.id,
                actor_user_id=actor_id,
            )

            assert closed.state == "CLOSED"
            assert closed.closed_at is not None
        finally:
            session.close()

    def test_close_return_triggers_clawback_for_direct(self, manage_auth):
        """close_return() triggers clawback for DIRECT orders."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="DIRECT")
            customer_return = _create_return(session, order_data, state="INSPECTED")

            from services.return_service import close_return
            actor_id = _get_system_user_id(session)
            close_return(
                session, customer_return.id,
                actor_user_id=actor_id,
            )

            # Verify clawback was created.
            clawback = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order_data["order_id"],
                    CommissionTransaction.state_event == "CLAWED_BACK",
                )
            ).scalar_one_or_none()
            assert clawback is not None
            assert clawback.signed_amount < 0
        finally:
            session.close()

    def test_close_return_no_clawback_for_local(self, manage_auth):
        """close_return() does NOT trigger clawback for LOCAL orders."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="LOCAL")
            customer_return = _create_return(session, order_data, state="INSPECTED")

            from services.return_service import close_return
            actor_id = _get_system_user_id(session)
            close_return(
                session, customer_return.id,
                actor_user_id=actor_id,
            )

            # Verify no clawback.
            clawbacks = session.execute(
                select(CommissionTransaction).where(
                    CommissionTransaction.order_id == order_data["order_id"],
                    CommissionTransaction.state_event == "CLAWED_BACK",
                )
            ).scalars().all()
            assert len(clawbacks) == 0
        finally:
            session.close()

    def test_invalid_transition_rejected(self, manage_auth):
        """Invalid return state transition raises error."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="LOCAL")
            customer_return = _create_return(session, order_data, state="PENDING_APPROVAL")

            from services.return_service import close_return, InvalidReturnStateTransitionError
            actor_id = _get_system_user_id(session)
            with pytest.raises(InvalidReturnStateTransitionError):
                close_return(
                    session, customer_return.id,
                    actor_user_id=actor_id,
                )
        finally:
            session.close()

    def test_receive_return(self, manage_auth):
        """receive_return() transitions APPROVED → RECEIVED."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="LOCAL")
            customer_return = _create_return(session, order_data, state="APPROVED")

            from services.return_service import receive_return
            actor_id = _get_system_user_id(session)
            received = receive_return(
                session, customer_return.id,
                actor_user_id=actor_id,
            )

            assert received.state == "RECEIVED"
            assert received.received_at is not None
        finally:
            session.close()

    def test_inspect_return(self, manage_auth):
        """inspect_return() transitions RECEIVED → INSPECTED."""
        session = get_session_factory()()
        try:
            order_data = _create_direct_order_with_commission(session, order_type="LOCAL")
            customer_return = _create_return(session, order_data, state="RECEIVED")

            from services.return_service import inspect_return
            actor_id = _get_system_user_id(session)
            inspected = inspect_return(
                session, customer_return.id,
                actor_user_id=actor_id,
            )

            assert inspected.state == "INSPECTED"
        finally:
            session.close()
