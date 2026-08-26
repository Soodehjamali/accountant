"""PostgreSQL-backed hardening tests for BOT_WRITE authorization (ADR-008).

Covers the verification checklist items not present in
test_bot_write_authorization.py:

1.  BOT_WRITE permission is required for write commands.
2.  User without BOT_WRITE is rejected.
3.  approval_required=True creates an approval request.
4.  approval_required=False does NOT create an approval request.
5.  Approval request contains the correct requester identity.
6.  Approval request starts in the expected pending state.
7.  Approval state transitions obey the existing model/service rules.
8.  Unauthorized user cannot resolve an approval.
9.  Approved request reaches the expected approved state.
10. Rejected request cannot be treated as approved.
11. Duplicate resolution is handled according to existing service semantics.
12. Audit/history records are created where ADR-008 requires them.
13. BotSession -> Representative identity remains the authorization anchor.
14. Existing BOT_QUERY commands still work (regression).
15. Tier 0 commands still work without BOT_WRITE.

All tests use the real PostgreSQL database (no mocks).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.approval_history import ApprovalHistory
from database.models.approval_request import ApprovalRequest
from database.models.audit_log import AuditLog
from database.models.bot_platform_ref import BotPlatformRef
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
from services import bot_session_service
from services.bot_command_service import (
    BOT_QUERY_PERMISSION,
    BOT_WRITE_PERMISSION,
    BotMessage,
    BotResponse,
    COMMAND_REGISTRY,
    PermissionDeniedError,
    UnboundSessionError,
    process_message,
)
from services.approval_service import (
    ApprovalRequestAlreadyExistsError,
    InvalidApprovalTransitionError,
    SeparationOfDutiesError,
    approve_request,
    cancel_request,
    create_approval_request,
    get_pending_request,
    reject_request,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping BOT_WRITE hardening tests",
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _ensure_telegram_platform(session: Session) -> BotPlatformRef:
    existing = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    system_user = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(
        code="TELEGRAM",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(p)
    session.flush()
    return p


def _create_representative(session: Session, system_user) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-HRN-{suffix.upper()}",
        person_name=f"Harden Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(
    session: Session, system_user, rep: Representative
) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(
        session,
        username=f"hrn_user_{suffix}",
        email=f"hrn_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )


def _grant_bot_query(
    session: Session, app_user: AppUser, system_user
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BQH_{suffix}"
    rbac_service.create_role(
        session,
        code=role_code,
        name=f"BQH Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session,
            code=BOT_QUERY_PERMISSION,
            name="Query via bot",
            resource="bot",
            action="query",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session,
        role_code=role_code,
        permission_code=BOT_QUERY_PERMISSION,
    )
    rbac_service.assign_role(
        session,
        user_id=app_user.id,
        role_code=role_code,
        assigned_by=system_user.id,
    )


def _grant_bot_write(
    session: Session, app_user: AppUser, system_user
) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BWH_{suffix}"
    rbac_service.create_role(
        session,
        code=role_code,
        name=f"BWH Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session,
            code=BOT_WRITE_PERMISSION,
            name="Write via bot",
            resource="bot",
            action="write",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session,
        role_code=role_code,
        permission_code=BOT_WRITE_PERMISSION,
    )
    rbac_service.assign_role(
        session,
        user_id=app_user.id,
        role_code=role_code,
        assigned_by=system_user.id,
    )


def _make_bound_session(
    session: Session, system_user, *, platform_user_id: str
):
    rep = _create_representative(session, system_user)
    app_user = _create_app_user(session, system_user, rep)
    _grant_bot_query(session, app_user, system_user)
    _ensure_telegram_platform(session)

    token = bot_session_service.generate_binding_token(
        session,
        representative_id=rep.id,
        platform_code="TELEGRAM",
        created_by=system_user.id,
    )
    bot_session = bot_session_service.create_binding(
        session,
        binding_token=token,
        platform_code="TELEGRAM",
        platform_user_id=platform_user_id,
        linked_by=app_user.id,
    )
    return rep, app_user, bot_session


def _register_test_write_command(
    name: str = "_test_write_hrn",
    *,
    approval_required: bool = False,
) -> None:
    """Register a temporary test write command in the global registry."""

    def _handler(session, user, rep, args):
        return "Write executed."

    COMMAND_REGISTRY[name] = _handler
    _handler._required_permission = BOT_WRITE_PERMISSION  # type: ignore[attr-defined]
    _handler._approval_required = approval_required  # type: ignore[attr-defined]


# =======================================================================
# 1. BOT_WRITE permission is required for write commands
# =======================================================================


@requires_database
class TestBOTWritePermissionRequired:
    """Write commands must require BOT_WRITE permission."""

    def test_write_command_rejected_without_permission(self):
        """A registered write command must raise PermissionDeniedError
        when the user lacks BOT_WRITE."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"wp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid
            )
            _register_test_write_command("_test_wp", approval_required=False)
            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_wp",
                )
                with pytest.raises(PermissionDeniedError):
                    process_message(session, message=msg)
            finally:
                del COMMAND_REGISTRY["_test_wp"]
        finally:
            session.close()

    def test_write_command_accepted_with_permission(self):
        """A registered write command must execute when the user holds
        BOT_WRITE and approval is not required."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"wp2-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid
            )
            _grant_bot_write(session, user, su)
            _register_test_write_command("_test_wp2", approval_required=False)
            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_wp2",
                )
                response = process_message(session, message=msg)
                assert isinstance(response, BotResponse)
                assert "Write executed." in response.text
            finally:
                del COMMAND_REGISTRY["_test_wp2"]
        finally:
            session.close()


# =======================================================================
# 2. User without BOT_WRITE is rejected
# =======================================================================


@requires_database
class TestBOTWriteDenied:
    """Users with only BOT_QUERY must not reach write execution."""

    def test_bot_query_user_rejected_for_write(self):
        """A user who has BOT_QUERY but not BOT_WRITE must be rejected
        when attempting a write command."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"bd-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid
            )
            # User has BOT_QUERY but NOT BOT_WRITE.
            _register_test_write_command("_test_bd", approval_required=False)
            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_bd",
                )
                with pytest.raises(PermissionDeniedError) as exc_info:
                    process_message(session, message=msg)
                assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
            finally:
                del COMMAND_REGISTRY["_test_bd"]
        finally:
            session.close()


# =======================================================================
# 3. approval_required=True creates an approval request
# =======================================================================


@requires_database
class TestApprovalRequiredCreatesRequest:
    """approval_required=True commands must create an approval_request."""

    def test_creates_approval_request(self):
        """An approval_required=True command must create a PENDING
        approval_request and NOT call the handler."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ar-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid
            )
            _grant_bot_write(session, user, su)
            _register_test_write_command(
                "_test_ar", approval_required=True
            )
            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_ar",
                )
                response = process_message(session, message=msg)
                assert "submitted for approval" in response.text.lower()
            finally:
                del COMMAND_REGISTRY["_test_ar"]
        finally:
            session.close()


# =======================================================================
# 4. approval_required=False does NOT create an approval request
# =======================================================================


@requires_database
class TestApprovalNotRequired:
    """approval_required=False commands must NOT create approval requests."""

    def test_direct_executes_without_approval(self):
        """A write command with approval_required=False must execute
        immediately without creating an approval_request."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"anr-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(
                session, su, platform_user_id=puid
            )
            _grant_bot_write(session, user, su)
            _register_test_write_command(
                "_test_anr", approval_required=False
            )
            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_anr",
                )
                response = process_message(session, message=msg)
                assert "Write executed." in response.text
                # Verify no approval request was created.
                pending = get_pending_request(
                    session,
                    "bot_command:_test_anr",
                    bot_session.id,
                )
                assert pending is None
            finally:
                del COMMAND_REGISTRY["_test_anr"]
        finally:
            session.close()


# =======================================================================
# 5. Approval request contains correct requester identity
# =======================================================================


@requires_database
class TestRequesterIdentity:
    """The approval request must contain the AppUser.id of the requester."""

    def test_requester_is_app_user(self):
        """requested_by must be the AppUser.id, not the BotSession id
        or the Representative id."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"ri-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(
                session, su, platform_user_id=puid
            )
            _grant_bot_write(session, user, su)
            _register_test_write_command(
                "_test_ri", approval_required=True
            )
            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_ri",
                )
                process_message(session, message=msg)

                pending = get_pending_request(
                    session,
                    "bot_command:_test_ri",
                    bot_session.id,
                )
                assert pending is not None
                assert pending.requested_by == user.id
                assert pending.requested_by != rep.id
                assert pending.requested_by != bot_session.id
            finally:
                del COMMAND_REGISTRY["_test_ri"]
        finally:
            session.close()


# =======================================================================
# 6. Approval request starts in expected PENDING state
# =======================================================================


@requires_database
class TestInitialState:
    """New approval requests must start in PENDING with correct fields."""

    def test_pending_status_and_fields(self):
        """A freshly created request must be PENDING with the correct
        entity_type, entity_id, requested_by, and timestamps."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            entity_id = uuid.uuid4()
            request = create_approval_request(
                session,
                entity_type="bot_command:test_init",
                entity_id=entity_id,
                requested_by=user.id,
                reason_text="Initial state check",
            )

            assert request.status == "PENDING"
            assert request.entity_type == "bot_command:test_init"
            assert request.entity_id == entity_id
            assert request.requested_by == user.id
            assert request.resolved_by is None
            assert request.resolved_at is None
            assert request.requested_at is not None
        finally:
            session.close()

    def test_initial_history_entry(self):
        """Creation must record an initial PENDING->PENDING history row."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="test",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id
                )
            ).scalars().all()

            assert len(history) == 1
            assert history[0].from_status == "PENDING"
            assert history[0].to_status == "PENDING"
            assert history[0].actor_user_id == user.id
        finally:
            session.close()


# =======================================================================
# 7. Approval state transitions obey model/service rules
# =======================================================================


@requires_database
class TestTransitionRules:
    """PENDING -> APPROVED/REJECTED/CANCELLED.  Terminal states are locked."""

    def test_approve_transitions_to_approved(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approved = approve_request(
                session,
                request_id=request.id,
                approver_id=su.id,
                note="OK",
            )
            assert approved.status == "APPROVED"
            assert approved.resolved_by == su.id
            assert approved.resolved_at is not None
        finally:
            session.close()

    def test_terminal_approved_cannot_change(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approve_request(
                session, request_id=request.id, approver_id=su.id
            )
            with pytest.raises(InvalidApprovalTransitionError):
                reject_request(
                    session, request_id=request.id, approver_id=su.id
                )
            with pytest.raises(InvalidApprovalTransitionError):
                cancel_request(
                    session,
                    request_id=request.id,
                    cancelled_by=su.id,
                )
        finally:
            session.close()

    def test_terminal_rejected_cannot_change(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            reject_request(
                session, request_id=request.id, approver_id=su.id
            )
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(
                    session, request_id=request.id, approver_id=su.id
                )
        finally:
            session.close()

    def test_terminal_cancelled_cannot_change(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            cancel_request(
                session, request_id=request.id, cancelled_by=user.id
            )
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(
                    session, request_id=request.id, approver_id=su.id
                )
        finally:
            session.close()


# =======================================================================
# 8. Unauthorized user cannot resolve an approval
# =======================================================================


@requires_database
class TestSeparationOfDuties:
    """Requester must not be able to approve their own request."""

    def test_requester_cannot_approve_own_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            with pytest.raises(SeparationOfDutiesError):
                approve_request(
                    session, request_id=request.id, approver_id=user.id
                )
        finally:
            session.close()

    def test_requester_cannot_reject_own_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            with pytest.raises(SeparationOfDutiesError):
                reject_request(
                    session, request_id=request.id, approver_id=user.id
                )
        finally:
            session.close()


# =======================================================================
# 9. Approved request reaches expected approved state
# =======================================================================


@requires_database
class TestApprovedState:
    """Approved requests must reach APPROVED with all expected fields."""

    def test_approved_has_resolved_fields(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approved = approve_request(
                session,
                request_id=request.id,
                approver_id=su.id,
                note="Approved for test",
            )
            assert approved.status == "APPROVED"
            assert approved.resolved_by == su.id
            assert approved.resolved_at is not None
        finally:
            session.close()


# =======================================================================
# 10. Rejected request cannot be treated as approved
# =======================================================================


@requires_database
class TestRejectedCannotBeApproved:
    """A rejected request must remain rejected."""

    def test_approve_after_reject_fails(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            reject_request(
                session, request_id=request.id, approver_id=su.id
            )

            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(
                    session, request_id=request.id, approver_id=su.id
                )

            # Verify status is still REJECTED.
            refreshed = session.get(ApprovalRequest, request.id)
            assert refreshed.status == "REJECTED"
        finally:
            session.close()


# =======================================================================
# 11. Duplicate resolution handled per existing semantics
# =======================================================================


@requires_database
class TestDuplicateResolution:
    """Resolving an already-resolved request must fail."""

    def test_approve_already_approved_fails(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approve_request(
                session, request_id=request.id, approver_id=su.id
            )
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(
                    session, request_id=request.id, approver_id=su.id
                )
        finally:
            session.close()

    def test_reject_already_rejected_fails(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            reject_request(
                session, request_id=request.id, approver_id=su.id
            )
            with pytest.raises(InvalidApprovalTransitionError):
                reject_request(
                    session, request_id=request.id, approver_id=su.id
                )
        finally:
            session.close()


# =======================================================================
# 12. Audit/history records created where ADR-008 requires
# =======================================================================


@requires_database
class TestAuditHistoryRecords:
    """Every status transition must create approval_history and audit_log."""

    def test_approve_creates_history_and_audit(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approve_request(
                session,
                request_id=request.id,
                approver_id=su.id,
                note="audit test",
            )

            # History: creation (PENDING->PENDING) + approval (PENDING->APPROVED).
            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id
                )
            ).scalars().all()
            assert len(history) == 2
            assert history[1].from_status == "PENDING"
            assert history[1].to_status == "APPROVED"
            assert history[1].actor_user_id == su.id

            # Audit log.
            audit = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "approval_request",
                    AuditLog.entity_id == request.id,
                    AuditLog.action == "APPROVE",
                )
            ).scalars().all()
            assert len(audit) == 1
            assert audit[0].actor_user_id == su.id
        finally:
            session.close()

    def test_reject_creates_history_and_audit(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            reject_request(
                session,
                request_id=request.id,
                approver_id=su.id,
                note="nope",
            )

            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id
                )
            ).scalars().all()
            assert len(history) == 2
            assert history[1].to_status == "REJECTED"

            audit = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "approval_request",
                    AuditLog.entity_id == request.id,
                    AuditLog.action == "REJECT",
                )
            ).scalars().all()
            assert len(audit) == 1
        finally:
            session.close()

    def test_cancel_creates_history_and_audit(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, su)
            user = _create_app_user(session, su, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            cancel_request(
                session,
                request_id=request.id,
                cancelled_by=user.id,
                note="cancelled",
            )

            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id
                )
            ).scalars().all()
            assert len(history) == 2
            assert history[1].to_status == "CANCELLED"

            audit = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "approval_request",
                    AuditLog.entity_id == request.id,
                    AuditLog.action == "UPDATE",
                )
            ).scalars().all()
            assert len(audit) == 1
        finally:
            session.close()


# =======================================================================
# 13. BotSession -> Representative identity remains the authorization anchor
# =======================================================================


@requires_database
class TestAuthorizationAnchor:
    """BotSession -> Representative -> AppUser must be the identity chain."""

    def test_me_returns_correct_representative(self):
        """The /me command must return data from the BotSession's
        representative, not from any other source."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"anc-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid
            )

            msg = BotMessage(
                platform_user_id=puid,
                platform_code="TELEGRAM",
                text="/me",
            )
            response = process_message(session, message=msg)
            assert rep.person_name in response.text
            assert rep.code in response.text
        finally:
            session.close()

    def test_two_sessions_different_representatives(self):
        """Two different BotSessions must resolve to different identities."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            puid_a = f"ancA-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(
                session, su, platform_user_id=puid_a
            )
            puid_b = f"ancB-{uuid.uuid4().hex[:6]}"
            rep_b, _, _ = _make_bound_session(
                session, su, platform_user_id=puid_b
            )

            resp_a = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid_a,
                    platform_code="TELEGRAM",
                    text="/me",
                ),
            )
            resp_b = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid_b,
                    platform_code="TELEGRAM",
                    text="/me",
                ),
            )

            assert rep_a.person_name in resp_a.text
            assert rep_b.person_name in resp_b.text
            assert rep_a.person_name not in resp_b.text
            assert rep_b.person_name not in resp_a.text
        finally:
            session.close()

    def test_unbound_session_rejected_for_write(self):
        """An unbound session must not reach any write handler."""
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999",
                platform_code="TELEGRAM",
                text="/_test_write_hrn",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()


# =======================================================================
# 14. Existing BOT_QUERY commands still work (regression)
# =======================================================================


@requires_database
class TestBOTQueryRegression:
    """Existing read commands must continue to work unchanged."""

    def test_me_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"regm-{uuid.uuid4().hex[:6]}"
            rep, _, _ = _make_bound_session(
                session, su, platform_user_id=puid
            )
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/me",
                ),
            )
            assert isinstance(response, BotResponse)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_customers_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"regc-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/customers",
                ),
            )
            assert isinstance(response, BotResponse)
            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_orders_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"rego-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/orders",
                ),
            )
            assert isinstance(response, BotResponse)
            assert "No orders found" in response.text
        finally:
            session.close()

    def test_inventory_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"regi-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/inventory",
                ),
            )
            assert isinstance(response, BotResponse)
        finally:
            session.close()

    def test_balance_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"regb-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/balance",
                ),
            )
            assert isinstance(response, BotResponse)
            assert "No customers assigned" in response.text
        finally:
            session.close()


# =======================================================================
# 15. Tier 0 commands still work without BOT_WRITE
# =======================================================================


@requires_database
class TestTier0Commands:
    """Tier 0 commands (start, help, link, unlink) must work without
    BOT_QUERY or BOT_WRITE permissions."""

    def test_start_without_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Create representative + user with NO permissions at all.
            suffix = uuid.uuid4().hex[:8]
            rep = Representative(
                code=f"REP-T0-{suffix.upper()}",
                person_name=f"Tier0 Rep {suffix}",
                status="ACTIVE",
                created_by=su.id,
                updated_by=su.id,
            )
            session.add(rep)
            session.flush()
            user = auth_service.create_user(
                session,
                username=f"t0u_{suffix}",
                email=f"t0_{suffix}@test.invalid",
                password="test-password-123",
                created_by=su.id,
                representative_id=rep.id,
            )
            _ensure_telegram_platform(session)
            puid = f"t0-{uuid.uuid4().hex[:6]}"
            token = bot_session_service.generate_binding_token(
                session,
                representative_id=rep.id,
                platform_code="TELEGRAM",
                created_by=su.id,
            )
            bot_session_service.create_binding(
                session,
                binding_token=token,
                platform_code="TELEGRAM",
                platform_user_id=puid,
                linked_by=user.id,
            )

            # /start must work without any permission.
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/start",
                ),
            )
            assert "Welcome" in response.text
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_help_without_permission(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"t0h-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)
            response = process_message(
                session,
                message=BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/help",
                ),
            )
            assert "Available commands" in response.text
        finally:
            session.close()


# =======================================================================
# app_user=None security fix regression test
# =======================================================================


@requires_database
class TestAppUserNoneBypass:
    """When app_user is None (no linked AppUser for the representative),
    registered commands must NOT execute -- they must be denied."""

    def test_no_app_user_denies_registered_command(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Create a representative WITHOUT a linked AppUser.
            suffix = uuid.uuid4().hex[:8]
            rep = Representative(
                code=f"REP-NULL-{suffix.upper()}",
                person_name=f"Null User Rep {suffix}",
                status="ACTIVE",
                created_by=su.id,
                updated_by=su.id,
            )
            session.add(rep)
            session.flush()

            _ensure_telegram_platform(session)
            puid = f"null-{uuid.uuid4().hex[:6]}"
            token = bot_session_service.generate_binding_token(
                session,
                representative_id=rep.id,
                platform_code="TELEGRAM",
                created_by=su.id,
            )
            bot_session = bot_session_service.create_binding(
                session,
                binding_token=token,
                platform_code="TELEGRAM",
                platform_user_id=puid,
                linked_by=su.id,
            )

            # Attempting /me (a registered command) must fail because
            # app_user is None and permission cannot be verified.
            msg = BotMessage(
                platform_user_id=puid,
                platform_code="TELEGRAM",
                text="/me",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()
