"""PostgreSQL-backed concurrency tests for approval resolution.

Tests whether two concurrent approval attempts against the same
approval_request can result in double-execution.

Uses two separate database sessions to simulate true concurrency.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.approval_request import ApprovalRequest
from database.models.order import Order
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
from services.approval_service import (
    InvalidApprovalTransitionError,
    SeparationOfDutiesError,
    approve_request,
    cancel_request,
    create_approval_request,
    reject_request,
)
from services.approval_execution_service import (
    execute_approved_request,
    EXECUTOR_REGISTRY,
    ApprovalNotApprovedError,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping approval concurrency tests",
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _create_test_fixtures(session: Session):
    """Create a system user, representative, app user, and register
    a test command executor."""
    system_user = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_rbac_bootstrap(session)

    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-CNV-{suffix.upper()}",
        person_name=f"Concurrency Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()

    user = auth_service.create_user(
        session,
        username=f"cnv_user_{suffix}",
        email=f"cnv_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )

    # Approver (different from requester).
    approver = auth_service.create_user(
        session,
        username=f"cnv_approver_{suffix}",
        email=f"cnv_appr_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )

    session.flush()
    return system_user, rep, user, approver


def _register_test_executor():
    """Register a test command executor that counts executions."""
    execution_count = {"count": 0}
    order_ids_created = {"ids": []}

    def _test_executor(session, payload, actor_user_id):
        execution_count["count"] += 1
        # Create a real order to verify single-execution.
        from database.models.customer import Customer
        from database.models.price_history import PriceHistory
        from database.models.warehouse import Warehouse
        from services.order_service import create_order, OrderLineInput
        import decimal

        customer = session.get(Customer, uuid.UUID(payload["customer_id"]))
        if customer is None:
            # Create a minimal customer for the test.
            suffix = uuid.uuid4().hex[:8]
            customer = Customer(
                code=f"C-CNV-{suffix}",
                name=f"Concurrency Customer {suffix}",
                type="CORPORATE",
                currency_id=uuid.UUID(payload["currency_id"]),
                status="ACTIVE",
                created_by=actor_user_id,
                updated_by=actor_user_id,
            )
            session.add(customer)
            session.flush()

        order = create_order(
            session,
            customer_id=customer.id,
            representative_id=uuid.UUID(payload["representative_id"]),
            currency_id=uuid.UUID(payload["currency_id"]),
            order_type="LOCAL",
            fulfillment_mode="REP_LOCAL",
            sales_channel="BOT_TELEGRAM",
            lines=[
                OrderLineInput(
                    product_id=uuid.UUID(payload["product_id"]),
                    fulfillment_warehouse_id=uuid.UUID(payload["warehouse_id"]),
                    price_history_id=uuid.UUID(payload["price_history_id"]),
                    qty_ordered=decimal.Decimal(str(payload["qty"])),
                    fulfillment_mode="REP_LOCAL",
                ),
            ],
            created_by=actor_user_id,
        )
        order_ids_created["ids"].append(str(order.id))
        return f"Order {order.order_number} created"

    EXECUTOR_REGISTRY["create-order"] = _test_executor
    return execution_count, order_ids_created


# =======================================================================
# Race condition tests
# =======================================================================


@requires_database
class TestApprovalRaceCondition:
    """Test whether concurrent approvals can cause double-execution."""

    def test_sequential_approve_reject_second_fails(self):
        """Sequential: approve first, then reject must fail."""
        factory = get_session_factory()
        session1 = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session1)
            system_user, rep, user, approver = _create_test_fixtures(session1)

            request = create_approval_request(
                session1,
                entity_type="test-sequential",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            session1.commit()

            # Session 2: approve.
            session2 = factory()
            try:
                approve_request(
                    session2,
                    request_id=request.id,
                    approver_id=approver.id,
                )
                session2.commit()
            finally:
                session2.close()

            # Session 3: try to reject (should fail).
            session3 = factory()
            try:
                with pytest.raises(InvalidApprovalTransitionError):
                    reject_request(
                        session3,
                        request_id=request.id,
                        approver_id=approver.id,
                    )
            finally:
                session3.close()

            # Verify final state is APPROVED.
            session4 = factory()
            try:
                final = session4.get(ApprovalRequest, request.id)
                assert final.status == "APPROVED"
            finally:
                session4.close()
        finally:
            session1.close()

    def test_optimistic_lock_prevents_concurrent_approve(self):
        """Two concurrent approve calls: exactly one must succeed.

        Uses SQLAlchemy's version_id_col optimistic locking on
        approval_request. When both sessions load version=1, the
        first UPDATE succeeds (version -> 2). The second UPDATE
        (WHERE version = 1) finds zero rows and raises StaleDataError.
        """
        from sqlalchemy.orm.exc import StaleDataError

        factory = get_session_factory()
        session1 = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session1)
            system_user, rep, user, approver = _create_test_fixtures(session1)

            request = create_approval_request(
                session1,
                entity_type="test-race-approve",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            session1.commit()
            request_id = request.id
        finally:
            session1.close()

        results = {"session1": None, "session2": None}

        def approve_in_session(session_factory, label):
            session = session_factory()
            try:
                try:
                    approve_request(
                        session,
                        request_id=request_id,
                        approver_id=approver.id,
                    )
                    session.commit()
                    results[label] = "committed"
                except StaleDataError:
                    session.rollback()
                    results[label] = "stale_data_error"
                except InvalidApprovalTransitionError:
                    # Second thread sees the already-committed terminal
                    # state (APPROVED).  This is the common outcome under
                    # PostgreSQL READ COMMITTED: the second session's
                    # SELECT reads the committed state of the first
                    # transaction, so it raises an application-level
                    # transition error rather than a DB-level stale-data
                    # error.  Both outcomes prevent double-execution.
                    session.rollback()
                    results[label] = "transition_error"
                except Exception as e:
                    session.rollback()
                    results[label] = f"error: {e}"
            finally:
                session.close()

        # Both threads attempt to approve concurrently.
        t1 = threading.Thread(target=approve_in_session, args=(factory, "session1"))
        t2 = threading.Thread(target=approve_in_session, args=(factory, "session2"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one must succeed.  The other must fail with either
        # StaleDataError (optimistic lock) or InvalidApprovalTransitionError
        # (already-transitioned).  Both prevent double-execution.
        succeeded = sum(1 for v in results.values() if v == "committed")
        prevented = sum(1 for v in results.values() if v in ("stale_data_error", "transition_error"))
        assert succeeded == 1, f"Expected exactly 1 success, got {succeeded}. Results: {results}"
        assert prevented == 1, f"Expected exactly 1 prevention, got {prevented}. Results: {results}"

        # Verify final state is APPROVED.
        session_check = factory()
        try:
            final = session_check.get(ApprovalRequest, request_id)
            assert final.status == "APPROVED"
        finally:
            session_check.close()

    def test_optimistic_lock_prevents_concurrent_approve_reject(self):
        """Concurrent approve vs reject: exactly one must succeed."""
        from sqlalchemy.orm.exc import StaleDataError

        factory = get_session_factory()
        session1 = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session1)
            system_user, rep, user, approver = _create_test_fixtures(session1)

            request = create_approval_request(
                session1,
                entity_type="test-race-approve-reject",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            session1.commit()
            request_id = request.id
        finally:
            session1.close()

        results = {"approve": None, "reject": None}

        def try_approve():
            session = factory()
            try:
                try:
                    approve_request(
                        session, request_id=request_id, approver_id=approver.id,
                    )
                    session.commit()
                    results["approve"] = "committed"
                except (StaleDataError, InvalidApprovalTransitionError):
                    session.rollback()
                    results["approve"] = "prevented"
                except Exception:
                    session.rollback()
                    results["approve"] = "error"
            finally:
                session.close()

        def try_reject():
            session = factory()
            try:
                try:
                    reject_request(
                        session, request_id=request_id, approver_id=approver.id,
                    )
                    session.commit()
                    results["reject"] = "committed"
                except (StaleDataError, InvalidApprovalTransitionError):
                    session.rollback()
                    results["reject"] = "prevented"
                except Exception:
                    session.rollback()
                    results["reject"] = "error"
            finally:
                session.close()

        t1 = threading.Thread(target=try_approve)
        t2 = threading.Thread(target=try_reject)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one must succeed.
        succeeded = sum(1 for v in results.values() if v == "committed")
        prevented = sum(1 for v in results.values() if v == "prevented")
        assert succeeded == 1, f"Expected exactly 1 success, got {succeeded}. Results: {results}"
        assert prevented == 1, f"Expected exactly 1 prevention, got {prevented}. Results: {results}"

        # Verify final state is terminal.
        session_check = factory()
        try:
            final = session_check.get(ApprovalRequest, request_id)
            assert final.status in ("APPROVED", "REJECTED")
        finally:
            session_check.close()

    def test_optimistic_lock_prevents_concurrent_approve_cancel(self):
        """Concurrent approve vs cancel: exactly one must succeed."""
        from sqlalchemy.orm.exc import StaleDataError

        factory = get_session_factory()
        session1 = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session1)
            system_user, rep, user, approver = _create_test_fixtures(session1)

            request = create_approval_request(
                session1,
                entity_type="test-race-approve-cancel",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            session1.commit()
            request_id = request.id
        finally:
            session1.close()

        results = {"approve": None, "cancel": None}

        def try_approve():
            session = factory()
            try:
                try:
                    approve_request(
                        session, request_id=request_id, approver_id=approver.id,
                    )
                    session.commit()
                    results["approve"] = "committed"
                except (StaleDataError, InvalidApprovalTransitionError):
                    session.rollback()
                    results["approve"] = "prevented"
                except Exception:
                    session.rollback()
                    results["approve"] = "error"
            finally:
                session.close()

        def try_cancel():
            session = factory()
            try:
                try:
                    cancel_request(
                        session, request_id=request_id, cancelled_by=user.id,
                    )
                    session.commit()
                    results["cancel"] = "committed"
                except (StaleDataError, InvalidApprovalTransitionError):
                    session.rollback()
                    results["cancel"] = "prevented"
                except Exception:
                    session.rollback()
                    results["cancel"] = "error"
            finally:
                session.close()

        t1 = threading.Thread(target=try_approve)
        t2 = threading.Thread(target=try_cancel)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        succeeded = sum(1 for v in results.values() if v == "committed")
        prevented = sum(1 for v in results.values() if v == "prevented")
        assert succeeded == 1, f"Expected exactly 1 success, got {succeeded}. Results: {results}"
        assert prevented == 1, f"Expected exactly 1 prevention, got {prevented}. Results: {results}"

        session_check = factory()
        try:
            final = session_check.get(ApprovalRequest, request_id)
            assert final.status in ("APPROVED", "CANCELLED")
        finally:
            session_check.close()

    def test_duplicate_history_not_created_on_sequential_transitions(self):
        """After approval, a second transition attempt must fail and
        not create duplicate history entries."""
        factory = get_session_factory()
        session = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user, rep, user, approver = _create_test_fixtures(session)

            request = create_approval_request(
                session,
                entity_type="test-dup-history",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            session.commit()

            # Approve.
            approve_request(
                session, request_id=request.id, approver_id=approver.id,
            )
            session.commit()

            # Count history entries.
            from database.models.approval_history import ApprovalHistory
            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id,
                )
            ).scalars().all()
            count_after_approve = len(history)

            # Try to approve again (should fail).
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(
                    session, request_id=request.id, approver_id=approver.id,
                )

            # History count should not have changed.
            history_after = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id,
                )
            ).scalars().all()
            assert len(history_after) == count_after_approve
        finally:
            session.close()

    def test_concurrent_double_execution_prevented(self):
        """Two concurrent approve+execute calls: only one order created."""
        from sqlalchemy.orm.exc import StaleDataError

        factory = get_session_factory()

        # Set up test data.
        session_setup = factory()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session_setup)
            system_user, rep, user, approver = _create_test_fixtures(session_setup)

            # Create required FK targets.
            from database.models.customer import Customer
            from database.models.product import Product
            from database.models.price_history import PriceHistory
            from database.models.price_list import PriceList
            from database.models.warehouse import Warehouse
            from database.models.warehouse_assignment import WarehouseAssignment
            from database.models.customer_rep_assignment import CustomerRepAssignment
            import decimal
            from datetime import datetime, timezone, timedelta

            customer = Customer(
                code=f"C-EXEC-{uuid.uuid4().hex[:6]}",
                name="Execution Test Customer",
                type="CORPORATE",
                currency_id=bootstrap_service.ensure_default_currency(
                    session_setup, actor_id=system_user.id
                ).id,
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session_setup.add(customer)
            session_setup.flush()

            # Assign customer to rep.
            session_setup.add(CustomerRepAssignment(
                customer_id=customer.id,
                representative_id=rep.id,
                effective_from=datetime.now(timezone.utc) - timedelta(days=30),
                priority=1,
                created_by=system_user.id,
                updated_by=system_user.id,
            ))

            warehouse = bootstrap_service.ensure_default_warehouse(
                session_setup, actor_id=system_user.id,
            )
            session_setup.add(WarehouseAssignment(
                representative_id=rep.id,
                warehouse_id=warehouse.id,
                is_primary=True,
                effective_from=datetime.now(timezone.utc) - timedelta(days=30),
                created_by=system_user.id,
                updated_by=system_user.id,
            ))

            product = Product(
                sku=f"SKU-EXEC-{uuid.uuid4().hex[:6]}",
                name="Execution Test Product",
                base_uom_id=bootstrap_service.ensure_default_uom(
                    session_setup, actor_id=system_user.id
                ).id,
                status="ACTIVE",
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session_setup.add(product)
            session_setup.flush()

            price_list = PriceList(
                name=f"PL-EXEC-{uuid.uuid4().hex[:6]}",
                price_type="RETAIL",
                currency_id=customer.currency_id,
                owner_scope="GLOBAL",
                is_active=True,
                created_by=system_user.id,
                updated_by=system_user.id,
            )
            session_setup.add(price_list)
            session_setup.flush()

            price = PriceHistory(
                product_id=product.id,
                price_list_id=price_list.id,
                currency_id=customer.currency_id,
                price_type="RETAIL",
                unit_price=decimal.Decimal("50.0000"),
                effective_from=datetime.now(timezone.utc),
                created_by=system_user.id,
            )
            session_setup.add(price)
            session_setup.flush()

            # Create approval request with full payload.
            payload = {
                "customer_id": str(customer.id),
                "customer_code": customer.code,
                "product_id": str(product.id),
                "product_sku": product.sku,
                "qty": 5,
                "fulfillment_mode": "REP_LOCAL",
                "warehouse_id": str(warehouse.id),
                "warehouse_code": warehouse.code,
                "price_history_id": str(price.id),
                "currency_id": str(customer.currency_id),
                "representative_id": str(rep.id),
                "sales_channel": "BOT_TELEGRAM",
                "order_type": "LOCAL",
            }

            request = create_approval_request(
                session_setup,
                entity_type="bot_command:create-order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
                payload=payload,
            )
            session_setup.commit()
            request_id = request.id
        finally:
            session_setup.close()

        # Register executor.
        execution_count, order_ids_created = _register_test_executor()

        # Simulate concurrent approve+execute.
        results = {"s1": None, "s2": None}

        def approve_and_execute(label):
            session = factory()
            try:
                try:
                    approve_request(
                        session, request_id=request_id, approver_id=approver.id,
                    )
                    session.commit()
                    # Now execute.
                    execute_approved_request(
                        session, request_id=request_id, approver_id=approver.id,
                    )
                    session.commit()
                    results[label] = "committed"
                except StaleDataError:
                    session.rollback()
                    results[label] = "stale_data_error"
                except InvalidApprovalTransitionError:
                    session.rollback()
                    results[label] = "transition_error"
                except Exception as e:
                    session.rollback()
                    results[label] = f"error: {e}"
            finally:
                session.close()

        t1 = threading.Thread(target=approve_and_execute, args=("s1",))
        t2 = threading.Thread(target=approve_and_execute, args=("s2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Verify exactly one succeeded.
        succeeded = sum(1 for v in results.values() if v == "committed")
        prevented = sum(1 for v in results.values() if v in ("stale_data_error", "transition_error"))
        assert succeeded == 1, f"Expected exactly 1 success, got {succeeded}. Results: {results}"
        assert prevented == 1, f"Expected exactly 1 prevention, got {prevented}. Results: {results}"

        # Verify exactly one order was created.
        assert execution_count["count"] == 1, (
            f"Expected 1 execution, got {execution_count['count']}"
        )

        # Verify the approval request is in a terminal state.
        session_check = factory()
        try:
            final = session_check.get(ApprovalRequest, request_id)
            assert final.status in ("APPROVED", "REJECTED", "CANCELLED")
        finally:
            session_check.close()
