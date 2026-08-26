"""Architecture-level tests for BOT_WRITE authorization and approval workflow.

Covers ADR-008 design:
- BOT_WRITE permission is seeded but NOT granted to ADMIN by default.
- approval_service CRUD lifecycle: create, approve, reject, cancel.
- Separation of duties enforcement (approver != requester).
- Duplicate PENDING request prevention.
- Bot command framework: approval_required metadata on handlers.
- Bot command framework: approval-gated commands create approval_request
  instead of executing directly.
- Existing BOT_QUERY commands remain unaffected (no regression).

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
    UnboundSessionError,
    process_message,
)
from services.approval_service import (
    ApprovalRequestAlreadyExistsError,
    ApprovalRequestNotFoundError,
    InvalidApprovalTransitionError,
    SeparationOfDutiesError,
    approve_request,
    cancel_request,
    create_approval_request,
    get_pending_request,
    list_pending_requests,
    reject_request,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping BOT_WRITE authorization tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def _ensure_telegram_platform(session: Session) -> BotPlatformRef:
    existing = session.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    system_user = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=system_user.id, updated_by=system_user.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session: Session, system_user) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-BW-{suffix.upper()}",
        person_name=f"BW Test Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session: Session, system_user, rep: Representative) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    user = auth_service.create_user(
        session,
        username=f"bw_user_{suffix}",
        email=f"bw_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )
    return user


def _grant_bot_query(session: Session, app_user: AppUser, system_user) -> None:
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BWQ_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"BWQ Tester {suffix}",
        created_by=system_user.id,
    )
    try:
        rbac_service.create_permission(
            session, code=BOT_QUERY_PERMISSION,
            name="Query via bot", resource="bot", action="query",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session, role_code=role_code, permission_code=BOT_QUERY_PERMISSION,
    )
    rbac_service.assign_role(
        session, user_id=app_user.id, role_code=role_code,
        assigned_by=system_user.id,
    )


def _make_bound_session(session: Session, system_user, *, platform_user_id: str):
    rep = _create_representative(session, system_user)
    app_user = _create_app_user(session, system_user, rep)
    _grant_bot_query(session, app_user, system_user)
    _ensure_telegram_platform(session)

    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=system_user.id,
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=app_user.id,
    )
    return rep, app_user, bot_session


# ===========================================================================
# Permission Seeding Tests
# ===========================================================================


@requires_database
class TestBOTWritePermissionSeeding:
    """BOT_WRITE is seeded in the permission table but NOT granted to ADMIN."""

    def test_bot_write_permission_exists(self):
        """BOT_WRITE permission row must exist after bootstrap."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            from database.models.permission import Permission
            perm = session.execute(
                select(Permission).where(Permission.code == BOT_WRITE_PERMISSION)
            ).scalar_one_or_none()
            assert perm is not None, "BOT_WRITE permission should be seeded"
            assert perm.resource == "bot"
            assert perm.action == "write"
        finally:
            session.close()

    def test_bot_write_not_granted_to_admin(self):
        """ADMIN role must NOT hold BOT_WRITE permission."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Get ADMIN role's permission codes.
            perm_codes = rbac_service.get_user_permission_codes(
                session, system_user.id,
            )
            assert BOT_WRITE_PERMISSION not in perm_codes, (
                "BOT_WRITE must NOT be granted to ADMIN by default "
                "(ADR-008 acceptance criteria)"
            )
        finally:
            session.close()

    def test_bot_query_still_granted_to_admin(self):
        """BOT_QUERY should still be granted to ADMIN (no regression)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            perm_codes = rbac_service.get_user_permission_codes(
                session, system_user.id,
            )
            assert BOT_QUERY_PERMISSION in perm_codes
        finally:
            session.close()

    def test_bot_manage_still_granted_to_admin(self):
        """BOT_MANAGE should still be granted to ADMIN (no regression)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            perm_codes = rbac_service.get_user_permission_codes(
                session, system_user.id,
            )
            assert "BOT_MANAGE" in perm_codes
        finally:
            session.close()


# ===========================================================================
# Command Registry Metadata Tests (no DB needed)
# ===========================================================================


class TestCommandRegistryMetadata:
    """Verify the _required_permission and _approval_required metadata
    on registered commands."""

    def test_all_commands_have_required_permission(self):
        for name, handler in COMMAND_REGISTRY.items():
            perm = getattr(handler, "_required_permission", None)
            assert perm is not None, (
                f"Command '{name}' missing _required_permission"
            )

    def test_read_commands_default_approval_not_required(self):
        """Read-only commands should not require approval."""
        for name in ("me", "balance", "orders", "order", "inventory", "customers"):
            handler = COMMAND_REGISTRY[name]
            assert getattr(handler, "_approval_required", False) is False, (
                f"Read command '{name}' should not require approval"
            )

    def test_bot_write_permission_constant_defined(self):
        """BOT_WRITE_PERMISSION constant must exist and have the right value."""
        assert BOT_WRITE_PERMISSION == "BOT_WRITE"


# ===========================================================================
# Approval Service Tests
# ===========================================================================


@requires_database
class TestApprovalServiceCRUD:
    """Approval service: create, approve, reject, cancel lifecycle."""

    def test_create_approval_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            entity_id = uuid.uuid4()
            request = create_approval_request(
                session,
                entity_type="test_entity",
                entity_id=entity_id,
                requested_by=system_user.id,
                reason_text="Test approval",
            )
            assert request.status == "PENDING"
            assert request.entity_type == "test_entity"
            assert request.entity_id == entity_id
            assert request.requested_by == system_user.id
            assert request.reason_text == "Test approval"
        finally:
            session.close()

    def test_create_approval_request_records_history(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            request = create_approval_request(
                session,
                entity_type="test_entity",
                entity_id=uuid.uuid4(),
                requested_by=system_user.id,
            )
            history = session.execute(
                select(ApprovalHistory).where(
                    ApprovalHistory.approval_request_id == request.id,
                )
            ).scalars().all()
            assert len(history) == 1
            assert history[0].from_status == "PENDING"
            assert history[0].to_status == "PENDING"
        finally:
            session.close()

    def test_approve_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            admin = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, admin)
            user = _create_app_user(session, admin, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approved = approve_request(
                session,
                request_id=request.id,
                approver_id=admin.id,
                note="Approved by admin",
            )
            assert approved.status == "APPROVED"
            assert approved.resolved_by == admin.id
            assert approved.resolved_at is not None
        finally:
            session.close()

    def test_reject_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            admin = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, admin)
            user = _create_app_user(session, admin, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            rejected = reject_request(
                session,
                request_id=request.id,
                approver_id=admin.id,
                note="Rejected: insufficient info",
            )
            assert rejected.status == "REJECTED"
            assert rejected.resolved_by == admin.id
        finally:
            session.close()

    def test_cancel_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            admin = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, admin)
            user = _create_app_user(session, admin, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            cancelled = cancel_request(
                session,
                request_id=request.id,
                cancelled_by=user.id,
                note="Changed my mind",
            )
            assert cancelled.status == "CANCELLED"
        finally:
            session.close()

    def test_terminal_states_cannot_transition(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            admin = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, admin)
            user = _create_app_user(session, admin, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approve_request(
                session, request_id=request.id, approver_id=admin.id,
            )

            # Cannot approve again.
            with pytest.raises(InvalidApprovalTransitionError):
                approve_request(
                    session, request_id=request.id, approver_id=admin.id,
                )
        finally:
            session.close()

    def test_separation_of_duties_enforced(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            admin = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, admin)
            user = _create_app_user(session, admin, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            # Requester cannot approve their own request.
            with pytest.raises(SeparationOfDutiesError):
                approve_request(
                    session, request_id=request.id, approver_id=user.id,
                )
        finally:
            session.close()

    def test_duplicate_pending_request_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            entity_id = uuid.uuid4()
            create_approval_request(
                session,
                entity_type="order",
                entity_id=entity_id,
                requested_by=system_user.id,
            )
            with pytest.raises(ApprovalRequestAlreadyExistsError):
                create_approval_request(
                    session,
                    entity_type="order",
                    entity_id=entity_id,
                    requested_by=system_user.id,
                )
        finally:
            session.close()

    def test_get_pending_request(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            entity_id = uuid.uuid4()
            created = create_approval_request(
                session,
                entity_type="order",
                entity_id=entity_id,
                requested_by=system_user.id,
            )
            found = get_pending_request(session, "order", entity_id)
            assert found is not None
            assert found.id == created.id

            # After approval, no longer PENDING.
            # Use a different user as approver (separation of duties).
            rep2 = _create_representative(session, system_user)
            admin_user = _create_app_user(session, system_user, rep2)
            approve_request(
                session, request_id=created.id, approver_id=admin_user.id,
            )
            found_after = get_pending_request(session, "order", entity_id)
            assert found_after is None
        finally:
            session.close()

    def test_list_pending_requests(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Create 2 pending requests.
            create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=system_user.id,
            )
            create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=system_user.id,
            )
            pending = list_pending_requests(session)
            assert len(pending) >= 2
        finally:
            session.close()

    def test_nonexistent_request_raises(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            with pytest.raises(ApprovalRequestNotFoundError):
                approve_request(
                    session,
                    request_id=uuid.uuid4(),
                    approver_id=uuid.uuid4(),
                )
        finally:
            session.close()

    def test_approve_records_audit_log(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            admin = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, admin)
            user = _create_app_user(session, admin, rep)

            request = create_approval_request(
                session,
                entity_type="order",
                entity_id=uuid.uuid4(),
                requested_by=user.id,
            )
            approve_request(
                session, request_id=request.id, approver_id=admin.id,
            )

            from database.models.audit_log import AuditLog
            audit_entries = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "approval_request",
                    AuditLog.entity_id == request.id,
                    AuditLog.action == "APPROVE",
                )
            ).scalars().all()
            assert len(audit_entries) == 1
            assert audit_entries[0].actor_user_id == admin.id
        finally:
            session.close()


# ===========================================================================
# Approval-Gated Bot Command Tests
# ===========================================================================


@requires_database
class TestApprovalGatedCommands:
    """Verify that approval_required=True commands create an approval
    request instead of executing the mutation."""

    def test_approval_required_command_creates_request(self):
        """A command with approval_required=True should create an
        approval_request and return a pending message."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"appr-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(
                session, system_user, platform_user_id=puid,
            )

            # Temporarily register a test command with approval_required.
            from services.bot_command_service import BOT_WRITE_PERMISSION

            # Grant BOT_WRITE to the user.
            suffix = uuid.uuid4().hex[:8]
            role_code = f"BWW_{suffix}"
            rbac_service.create_role(
                session, code=role_code, name=f"BW Tester {suffix}",
                created_by=system_user.id,
            )
            try:
                rbac_service.create_permission(
                    session, code=BOT_WRITE_PERMISSION,
                    name="Write via bot", resource="bot", action="write",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code,
                permission_code=BOT_WRITE_PERMISSION,
            )
            rbac_service.assign_role(
                session, user_id=user.id, role_code=role_code,
                assigned_by=system_user.id,
            )

            # Register a test write command with approval_required=True.
            def _fake_write_handler(session, user, rep, args):
                return "This should not execute directly."

            COMMAND_REGISTRY["_test_write_cmd"] = _fake_write_handler
            _fake_write_handler._required_permission = BOT_WRITE_PERMISSION
            _fake_write_handler._approval_required = True

            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_write_cmd",
                )
                response = process_message(session, message=msg)
                assert isinstance(response, BotResponse)
                assert "submitted for approval" in response.text.lower()

                # Verify an approval request was created.
                pending = get_pending_request(
                    session,
                    "bot_command:_test_write_cmd",
                    bot_session.id,
                )
                assert pending is not None
                assert pending.requested_by == user.id
            finally:
                del COMMAND_REGISTRY["_test_write_cmd"]

        finally:
            session.close()

    def test_approval_required_command_duplicate_returns_pending(self):
        """Second call to same approval_required command while first is
        still pending should return 'already pending'."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"dup-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(
                session, system_user, platform_user_id=puid,
            )

            from services.bot_command_service import BOT_WRITE_PERMISSION

            # Grant BOT_WRITE.
            suffix = uuid.uuid4().hex[:8]
            role_code = f"BWD_{suffix}"
            rbac_service.create_role(
                session, code=role_code, name=f"BWD Tester {suffix}",
                created_by=system_user.id,
            )
            try:
                rbac_service.create_permission(
                    session, code=BOT_WRITE_PERMISSION,
                    name="Write via bot", resource="bot", action="write",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code,
                permission_code=BOT_WRITE_PERMISSION,
            )
            rbac_service.assign_role(
                session, user_id=user.id, role_code=role_code,
                assigned_by=system_user.id,
            )

            def _fake_write_handler2(session, user, rep, args):
                return "Should not execute."

            COMMAND_REGISTRY["_test_write_dup"] = _fake_write_handler2
            _fake_write_handler2._required_permission = BOT_WRITE_PERMISSION
            _fake_write_handler2._approval_required = True

            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_write_dup",
                )
                response1 = process_message(session, message=msg)
                assert "submitted for approval" in response1.text.lower()

                # Second call should say already pending.
                response2 = process_message(session, message=msg)
                assert "already pending" in response2.text.lower()
            finally:
                del COMMAND_REGISTRY["_test_write_dup"]

        finally:
            session.close()

    def test_write_without_approval_required_executes_directly(self):
        """A command with approval_required=False (default) but BOT_WRITE
        permission executes directly without creating an approval request."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"dir-{uuid.uuid4().hex[:6]}"
            rep, user, bot_session = _make_bound_session(
                session, system_user, platform_user_id=puid,
            )

            from services.bot_command_service import BOT_WRITE_PERMISSION

            # Grant BOT_WRITE.
            suffix = uuid.uuid4().hex[:8]
            role_code = f"BWD_{suffix}"
            rbac_service.create_role(
                session, code=role_code, name=f"BWD Tester {suffix}",
                created_by=system_user.id,
            )
            try:
                rbac_service.create_permission(
                    session, code=BOT_WRITE_PERMISSION,
                    name="Write via bot", resource="bot", action="write",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code,
                permission_code=BOT_WRITE_PERMISSION,
            )
            rbac_service.assign_role(
                session, user_id=user.id, role_code=role_code,
                assigned_by=system_user.id,
            )

            def _fake_direct_handler(session, user, rep, args):
                return "Executed directly!"

            COMMAND_REGISTRY["_test_direct_write"] = _fake_direct_handler
            _fake_direct_handler._required_permission = BOT_WRITE_PERMISSION
            _fake_direct_handler._approval_required = False

            try:
                msg = BotMessage(
                    platform_user_id=puid,
                    platform_code="TELEGRAM",
                    text="/_test_direct_write",
                )
                response = process_message(session, message=msg)
                assert "Executed directly!" in response.text

                # No approval request should be created.
                pending = get_pending_request(
                    session,
                    "bot_command:_test_direct_write",
                    bot_session.id,
                )
                assert pending is None
            finally:
                del COMMAND_REGISTRY["_test_direct_write"]

        finally:
            session.close()


# ===========================================================================
# No Regression: Existing BOT_QUERY Commands
# ===========================================================================


@requires_database
class TestNoRegressionBotQuery:
    """Existing BOT_QUERY commands remain unaffected by BOT_WRITE changes."""

    def test_me_command_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"reg-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, system_user, platform_user_id=puid,
            )
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/me",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_customers_command_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"regc-{uuid.uuid4().hex[:6]}"
            _make_bound_session(
                session, system_user, platform_user_id=puid,
            )
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/customers",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "No customers assigned" in response.text
        finally:
            session.close()

    def test_orders_command_still_works(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"rego-{uuid.uuid4().hex[:6]}"
            _make_bound_session(
                session, system_user, platform_user_id=puid,
            )
            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/orders",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert "No orders found" in response.text
        finally:
            session.close()
