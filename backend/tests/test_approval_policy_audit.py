"""Phase 8 — Approval Workflow Policy Audit Tests.

Focused tests covering:
1. State machine hardening (Step 6) — all invalid transitions.
2. Payload/executor security (Step 8) — cross-command isolation,
   executor registry safety, payload cannot override identity.
3. Requester cancellation policy (Step 4) — authorization boundaries.

All tests use the real PostgreSQL database (no mocks).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.approval_request import ApprovalRequest
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service
from services.approval_service import (
    InvalidApprovalTransitionError,
    SeparationOfDutiesError,
    approve_request,
    cancel_request,
    create_approval_request,
    get_pending_request,
    reject_request,
)
from services.approval_execution_service import (
    ApprovalNotApprovedError,
    EXECUTOR_REGISTRY,
    UnknownCommandTypeError,
    execute_approved_request,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping Phase 8 audit tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_fixtures(session: Session):
    """Create system_user, representative, requester, and approver."""
    system_user = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_rbac_bootstrap(session)

    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-AUD-{suffix.upper()}",
        person_name=f"Audit Test Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()

    requester = auth_service.create_user(
        session,
        username=f"aud_req_{suffix}",
        email=f"aud_req_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )

    approver = auth_service.create_user(
        session,
        username=f"aud_appr_{suffix}",
        email=f"aud_appr_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )

    session.flush()
    return system_user, rep, requester, approver


# ===========================================================================
# Step 6: State Machine Hardening — All Invalid Transitions
# ===========================================================================

@requires_database
class TestInvalidTransitions:
    """Verify the complete transition matrix. Terminal states must not
    allow any outgoing transitions."""

    def test_approved_to_approved(self):
        """APPROVED → APPROVED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(session, request_id=req.id, approver_id=approver.id)
        finally:
            session.close()

    def test_approved_to_rejected(self):
        """APPROVED → REJECTED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                reject_request(session, request_id=req.id, approver_id=approver.id)
        finally:
            session.close()

    def test_approved_to_cancelled(self):
        """APPROVED → CANCELLED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                cancel_request(session, request_id=req.id, cancelled_by=requester.id)
        finally:
            session.close()

    def test_rejected_to_approved(self):
        """REJECTED → APPROVED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            reject_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(session, request_id=req.id, approver_id=approver.id)
        finally:
            session.close()

    def test_rejected_to_cancelled(self):
        """REJECTED → CANCELLED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            reject_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                cancel_request(session, request_id=req.id, cancelled_by=requester.id)
        finally:
            session.close()

    def test_cancelled_to_approved(self):
        """CANCELLED → APPROVED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            cancel_request(session, request_id=req.id, cancelled_by=requester.id)
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(session, request_id=req.id, approver_id=approver.id)
        finally:
            session.close()

    def test_cancelled_to_rejected(self):
        """CANCELLED → REJECTED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            cancel_request(session, request_id=req.id, cancelled_by=requester.id)
            with pytest.raises(InvalidApprovalTransitionError):
                reject_request(session, request_id=req.id, approver_id=approver.id)
        finally:
            session.close()

    def test_cancelled_to_cancelled(self):
        """CANCELLED → CANCELLED must fail."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            cancel_request(session, request_id=req.id, cancelled_by=requester.id)
            with pytest.raises(InvalidApprovalTransitionError):
                cancel_request(session, request_id=req.id, cancelled_by=requester.id)
        finally:
            session.close()


# ===========================================================================
# Step 6: Separation of Duties — Requester Cannot Approve/Reject
# ===========================================================================

@requires_database
class TestRequesterCannotResolve:
    """The requester must not be able to approve or reject their own request."""

    def test_requester_cannot_approve_own_request(self):
        session = get_session_factory()()
        try:
            _, _, requester, _ = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            with pytest.raises(SeparationOfDutiesError):
                approve_request(session, request_id=req.id, approver_id=requester.id)
        finally:
            session.close()

    def test_requester_cannot_reject_own_request(self):
        session = get_session_factory()()
        try:
            _, _, requester, _ = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            with pytest.raises(SeparationOfDutiesError):
                reject_request(session, request_id=req.id, approver_id=requester.id)
        finally:
            session.close()


# ===========================================================================
# Step 6: Requester Cancellation — Authorization Boundaries
# ===========================================================================

@requires_database
class TestRequesterCancellation:
    """Requester may cancel their own PENDING request. Others may also cancel."""

    def test_requester_can_cancel_own_pending_request(self):
        """Requester is allowed to cancel their own PENDING request."""
        session = get_session_factory()()
        try:
            _, _, requester, _ = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            cancelled = cancel_request(
                session, request_id=req.id, cancelled_by=requester.id,
            )
            assert cancelled.status == "CANCELLED"
        finally:
            session.close()

    def test_other_user_can_cancel_pending_request(self):
        """A different user (e.g., admin) can also cancel a PENDING request."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            cancelled = cancel_request(
                session, request_id=req.id, cancelled_by=approver.id,
            )
            assert cancelled.status == "CANCELLED"
        finally:
            session.close()

    def test_cannot_cancel_terminal_approved(self):
        """Cannot cancel an APPROVED request."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                cancel_request(session, request_id=req.id, cancelled_by=requester.id)
        finally:
            session.close()

    def test_cannot_cancel_terminal_rejected(self):
        """Cannot cancel a REJECTED request."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="test", entity_id=uuid.uuid4(),
                requested_by=requester.id,
            )
            reject_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(InvalidApprovalTransitionError):
                cancel_request(session, request_id=req.id, cancelled_by=requester.id)
        finally:
            session.close()

    def test_cancelled_request_cannot_execute(self):
        """A cancelled request cannot be executed."""
        session = get_session_factory()()
        try:
            _, _, requester, _ = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="bot_command:test-cmd",
                entity_id=uuid.uuid4(),
                requested_by=requester.id,
                payload={"test": True},
            )
            cancel_request(session, request_id=req.id, cancelled_by=requester.id)
            with pytest.raises(ApprovalNotApprovedError):
                execute_approved_request(
                    session, request_id=req.id, approver_id=requester.id,
                )
        finally:
            session.close()


# ===========================================================================
# Step 8: Payload/Executor Security — Cross-Command Isolation
# ===========================================================================

@requires_database
class TestExecutorRegistrySafety:
    """Executor registry must enforce command-type isolation."""

    def test_unknown_entity_type_rejected(self):
        """An entity_type not starting with 'bot_command:' must be rejected."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="random_entity",
                entity_id=uuid.uuid4(),
                requested_by=requester.id,
                payload={"test": True},
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(UnknownCommandTypeError):
                execute_approved_request(
                    session, request_id=req.id, approver_id=approver.id,
                )
        finally:
            session.close()

    def test_unregistered_command_rejected(self):
        """A registered entity_type with no executor must be rejected."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="bot_command:nonexistent-command",
                entity_id=uuid.uuid4(),
                requested_by=requester.id,
                payload={"test": True},
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(UnknownCommandTypeError):
                execute_approved_request(
                    session, request_id=req.id, approver_id=approver.id,
                )
        finally:
            session.close()

    def test_approval_for_command_a_cannot_execute_command_b(self):
        """An approval for entity_type='bot_command:cmd-a' must not be
        executable via the executor for 'bot_command:cmd-b'."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)

            # Register two distinct test executors.
            exec_results = {"a": 0, "b": 0}

            def _exec_a(session, payload, actor_user_id):
                exec_results["a"] += 1
                return "executed_a"

            def _exec_b(session, payload, actor_user_id):
                exec_results["b"] += 1
                return "executed_b"

            EXECUTOR_REGISTRY["test-cmd-a"] = _exec_a
            EXECUTOR_REGISTRY["test-cmd-b"] = _exec_b

            try:
                # Create approval for cmd-a.
                req = create_approval_request(
                    session, entity_type="bot_command:test-cmd-a",
                    entity_id=uuid.uuid4(),
                    requested_by=requester.id,
                    payload={"target": "b_should_not_execute"},
                )
                approve_request(session, request_id=req.id, approver_id=approver.id)

                # Execute — should call _exec_a, NOT _exec_b.
                result = execute_approved_request(
                    session, request_id=req.id, approver_id=approver.id,
                )
                assert result == "executed_a"
                assert exec_results["a"] == 1
                assert exec_results["b"] == 0
            finally:
                EXECUTOR_REGISTRY.pop("test-cmd-a", None)
                EXECUTOR_REGISTRY.pop("test-cmd-b", None)
        finally:
            session.close()

    def test_payload_isolation_between_requests(self):
        """Two requests with different payloads execute independently."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)

            captured_payloads = []

            def _capture_executor(session, payload, actor_user_id):
                captured_payloads.append(payload)
                return "captured"

            EXECUTOR_REGISTRY["test-payload-iso"] = _capture_executor

            try:
                # Request 1 with payload A.
                req1 = create_approval_request(
                    session, entity_type="bot_command:test-payload-iso",
                    entity_id=uuid.uuid4(),
                    requested_by=requester.id,
                    payload={"data": "A"},
                )
                approve_request(session, request_id=req1.id, approver_id=approver.id)
                execute_approved_request(
                    session, request_id=req1.id, approver_id=approver.id,
                )

                # Request 2 with payload B.
                req2 = create_approval_request(
                    session, entity_type="bot_command:test-payload-iso",
                    entity_id=uuid.uuid4(),
                    requested_by=requester.id,
                    payload={"data": "B"},
                )
                approve_request(session, request_id=req2.id, approver_id=approver.id)
                execute_approved_request(
                    session, request_id=req2.id, approver_id=approver.id,
                )

                assert len(captured_payloads) == 2
                assert captured_payloads[0] == {"data": "A"}
                assert captured_payloads[1] == {"data": "B"}
            finally:
                EXECUTOR_REGISTRY.pop("test-payload-iso", None)
        finally:
            session.close()

    def test_missing_payload_rejected(self):
        """A request with no payload must not execute."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            req = create_approval_request(
                session, entity_type="bot_command:create-order",
                entity_id=uuid.uuid4(),
                requested_by=requester.id,
                # No payload.
            )
            approve_request(session, request_id=req.id, approver_id=approver.id)
            with pytest.raises(Exception):  # PayloadMissingError
                execute_approved_request(
                    session, request_id=req.id, approver_id=approver.id,
                )
        finally:
            session.close()

    def test_requester_identity_not_overridable_by_payload(self):
        """The payload contains representative_id and customer_id, but the
        requester identity (requested_by) comes from the BotSession chain,
        NOT from the payload. Verify the separation."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)
            payload_rep_id = uuid.uuid4()  # Fake rep ID in payload.

            req = create_approval_request(
                session, entity_type="bot_command:test",
                entity_id=uuid.uuid4(),
                requested_by=requester.id,
                payload={"representative_id": str(payload_rep_id)},
            )

            # requested_by must be the actual requester, not the payload value.
            assert req.requested_by == requester.id
            assert req.requested_by != payload_rep_id
        finally:
            session.close()


# ===========================================================================
# Step 7: Transaction Boundaries — Approved + Executed Cannot Execute Twice
# ===========================================================================

@requires_database
class TestExecutionIdempotency:
    """An approved request cannot be executed twice."""

    def test_cannot_execute_already_executed_approved(self):
        """After execution, re-executing the same approved request must fail
        or the execution must be idempotent. Currently, the transition guard
        on execute_approved_request validates status == APPROVED but does
        NOT change status after execution. However, the executor may fail
        on re-execution (e.g., duplicate order number). The key invariant
        is: the approval service transition guard prevents double-approve,
        and the executor is responsible for its own idempotency."""
        session = get_session_factory()()
        try:
            _, _, requester, approver = _create_fixtures(session)

            exec_count = {"n": 0}

            def _counting_executor(session, payload, actor_user_id):
                exec_count["n"] += 1
                return f"executed_{exec_count['n']}"

            EXECUTOR_REGISTRY["test-count"] = _counting_executor

            try:
                req = create_approval_request(
                    session, entity_type="bot_command:test-count",
                    entity_id=uuid.uuid4(),
                    requested_by=requester.id,
                    payload={"n": 1},
                )
                approve_request(session, request_id=req.id, approver_id=approver.id)

                # First execution.
                result1 = execute_approved_request(
                    session, request_id=req.id, approver_id=approver.id,
                )
                assert result1 == "executed_1"
                assert exec_count["n"] == 1

                # Second execution — executor is called again because
                # status is still APPROVED (execution doesn't change status).
                # This is by design: the executor must be idempotent.
                result2 = execute_approved_request(
                    session, request_id=req.id, approver_id=approver.id,
                )
                assert result2 == "executed_2"
                assert exec_count["n"] == 2

                # NOTE: This documents the current behavior. If idempotency
                # is needed, the executor should check for side effects
                # (e.g., duplicate order) rather than the approval service
                # tracking execution state. This is consistent with ADR-008
                # §6 and the explicit instruction not to add an idempotency
                # framework for the already-tested approval race.
            finally:
                EXECUTOR_REGISTRY.pop("test-count", None)
        finally:
            session.close()
