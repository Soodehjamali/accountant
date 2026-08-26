"""Service layer for the approval workflow (``approval_request`` T25 /
``approval_history`` H7).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's job.

Design:
    ``create_approval_request()``
        Creates a new PENDING approval request for any approvable entity
        (polymorphic via ``entity_type`` + ``entity_id``). Enforces:
        - Exactly one PENDING request per ``(entity_type, entity_id)``
          at a time (DB partial unique index, plus app-level pre-check).
        - Separation of duties: ``requested_by`` is recorded; a separate
          approver will be assigned later.

    ``approve_request()``
        Transitions a PENDING request to APPROVED. Records the transition
        in ``approval_history`` (H7). Enforces separation of duties:
        ``approver_id != requested_by``.

    ``reject_request()``
        Transitions a PENDING request to REJECTED. Records in H7.

    ``cancel_request()``
        Transitions a PENDING request to CANCELLED. Records in H7.
        Callable by the requester or an admin.

    ``get_pending_request()``
        Returns the active PENDING request for a given entity, or None.

    ``list_pending_requests()``
        Returns all PENDING requests, optionally filtered by approver.

Authorization:
    This module does NOT check permissions -- that is the caller's
    responsibility. This module enforces data-integrity invariants
    (status transitions, separation of duties, uniqueness) only.

History recording:
    Every status transition writes an ``approval_history`` (H7) row
    via ``_record_history()``, mirroring the ``_transition`` choke-point
    pattern used by ``order_service``, ``invoice_service``, and
    ``stock_transfer_service``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.approval_history import ApprovalHistory
from database.models.approval_request import ApprovalRequest
from services import audit_service


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ApprovalRequestNotFoundError(LookupError):
    """Raised when no matching ``approval_request`` row exists."""

    def __init__(self, request_id: uuid.UUID) -> None:
        super().__init__(f"No approval_request with id '{request_id}' exists.")
        self.request_id = request_id


class ApprovalRequestAlreadyExistsError(ValueError):
    """Raised when a PENDING request already exists for the entity."""

    def __init__(self, entity_type: str, entity_id: uuid.UUID) -> None:
        super().__init__(
            f"A PENDING approval request already exists for "
            f"{entity_type}:{entity_id}."
        )
        self.entity_type = entity_type
        self.entity_id = entity_id


class InvalidApprovalTransitionError(ValueError):
    """Raised when a status transition is not allowed."""

    def __init__(self, current_status: str, requested_status: str) -> None:
        super().__init__(
            f"Cannot transition approval request from "
            f"'{current_status}' to '{requested_status}'."
        )
        self.current_status = current_status
        self.requested_status = requested_status


class SeparationOfDutiesError(PermissionError):
    """Raised when the approver is the same as the requester."""

    def __init__(self) -> None:
        super().__init__(
            "The approver must be a different user than the requester "
            "(separation of duties)."
        )


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

#: Status transitions allowed on ``approval_request``.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"APPROVED", "REJECTED", "CANCELLED"},
    # Terminal states -- no transitions out.
    "APPROVED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}


# ---------------------------------------------------------------------------
# History recording
# ---------------------------------------------------------------------------


def _record_history(
    session: Session,
    *,
    approval_request_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    from_status: str,
    to_status: str,
    note: str | None = None,
) -> ApprovalHistory:
    """Append an ``approval_history`` (H7) row for a status transition.

    Mirrors the ``_transition`` choke-point pattern used by order_service,
    invoice_service, and stock_transfer_service.
    """
    entry = ApprovalHistory(
        approval_request_id=approval_request_id,
        actor_user_id=actor_user_id,
        from_status=from_status,
        to_status=to_status,
        note=note,
    )
    session.add(entry)
    session.flush()
    return entry


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_approval_request(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    requested_by: uuid.UUID,
    reason_text: str | None = None,
    assigned_approver_id: uuid.UUID | None = None,
    threshold_marker: bool = False,
    payload: dict | None = None,
) -> ApprovalRequest:
    """Create a new PENDING approval request for any approvable entity.

    Enforces:
        - Exactly one PENDING request per ``(entity_type, entity_id)``
          at a time (DB partial unique index + app-level check).

    ``assigned_approver_id`` may be set later (e.g. when an admin picks
    up the request). The DB allows it to be NULL at creation time.

    Returns the created ``ApprovalRequest``.

    Raises:
        ApprovalRequestAlreadyExistsError: a PENDING request already
            exists for this entity.
    """
    # App-level uniqueness pre-check (matches the DB partial unique index).
    existing = get_pending_request(session, entity_type, entity_id)
    if existing is not None:
        raise ApprovalRequestAlreadyExistsError(entity_type, entity_id)

    approval_number = generate_approval_number(session)

    request = ApprovalRequest(
        entity_type=entity_type,
        entity_id=entity_id,
        requested_by=requested_by,
        assigned_approver_id=assigned_approver_id,
        reason_text=reason_text,
        threshold_marker=threshold_marker,
        status="PENDING",
        payload=payload,
        approval_number=approval_number,
        created_by=requested_by,
        updated_by=requested_by,
    )
    session.add(request)
    session.flush()

    # Record the initial PENDING state in history.
    _record_history(
        session,
        approval_request_id=request.id,
        actor_user_id=requested_by,
        from_status="PENDING",
        to_status="PENDING",
        note="Approval request created.",
    )

    return request


# ---------------------------------------------------------------------------
# Resolve (approve / reject / cancel)
# ---------------------------------------------------------------------------


def approve_request(
    session: Session,
    *,
    request_id: uuid.UUID,
    approver_id: uuid.UUID,
    note: str | None = None,
) -> ApprovalRequest:
    """Approve a PENDING approval request.

    Enforces:
        - Request must be in PENDING status.
        - ``approver_id`` must differ from ``requested_by`` (separation
          of duties, enforced at both app and DB level).

    Writes:
        - ``approval_history`` (H7) row with ``to_status='APPROVED'``.
        - ``audit_log`` (H6) row with ``action='APPROVE'``.

    Returns the updated ``ApprovalRequest``.
    """
    request = _get_request(session, request_id)

    if request.status != "PENDING":
        raise InvalidApprovalTransitionError(request.status, "APPROVED")

    # Separation of duties: approver ≠ requester.
    if approver_id == request.requested_by:
        raise SeparationOfDutiesError()

    from_status = request.status
    request.status = "APPROVED"
    request.resolved_by = approver_id
    request.resolved_at = datetime.now(timezone.utc)
    request.updated_by = approver_id
    session.flush()

    # Record in approval_history.
    _record_history(
        session,
        approval_request_id=request.id,
        actor_user_id=approver_id,
        from_status=from_status,
        to_status="APPROVED",
        note=note,
    )

    # Record in audit_log.
    audit_service.record(
        session,
        entity_type="approval_request",
        entity_id=request.id,
        action="APPROVE",
        actor_user_id=approver_id,
        before={"status": from_status},
        after={"status": "APPROVED", "reason": note},
    )

    return request


def reject_request(
    session: Session,
    *,
    request_id: uuid.UUID,
    approver_id: uuid.UUID,
    note: str | None = None,
) -> ApprovalRequest:
    """Reject a PENDING approval request.

    Same enforcement and recording as ``approve_request``, with
    ``to_status='REJECTED'``.
    """
    request = _get_request(session, request_id)

    if request.status != "PENDING":
        raise InvalidApprovalTransitionError(request.status, "REJECTED")

    if approver_id == request.requested_by:
        raise SeparationOfDutiesError()

    from_status = request.status
    request.status = "REJECTED"
    request.resolved_by = approver_id
    request.resolved_at = datetime.now(timezone.utc)
    request.updated_by = approver_id
    session.flush()

    _record_history(
        session,
        approval_request_id=request.id,
        actor_user_id=approver_id,
        from_status=from_status,
        to_status="REJECTED",
        note=note,
    )

    audit_service.record(
        session,
        entity_type="approval_request",
        entity_id=request.id,
        action="REJECT",
        actor_user_id=approver_id,
        before={"status": from_status},
        after={"status": "REJECTED", "reason": note},
    )

    return request


def cancel_request(
    session: Session,
    *,
    request_id: uuid.UUID,
    cancelled_by: uuid.UUID,
    note: str | None = None,
) -> ApprovalRequest:
    """Cancel a PENDING approval request.

    Callable by the requester or an admin. Writes approval_history
    and audit_log entries.
    """
    request = _get_request(session, request_id)

    if request.status != "PENDING":
        raise InvalidApprovalTransitionError(request.status, "CANCELLED")

    from_status = request.status
    request.status = "CANCELLED"
    request.resolved_by = cancelled_by
    request.resolved_at = datetime.now(timezone.utc)
    request.updated_by = cancelled_by
    session.flush()

    _record_history(
        session,
        approval_request_id=request.id,
        actor_user_id=cancelled_by,
        from_status=from_status,
        to_status="CANCELLED",
        note=note,
    )

    audit_service.record(
        session,
        entity_type="approval_request",
        entity_id=request.id,
        action="UPDATE",
        actor_user_id=cancelled_by,
        before={"status": from_status},
        after={"status": "CANCELLED", "reason": note},
    )

    return request


# ---------------------------------------------------------------------------
# Approval number generation
# ---------------------------------------------------------------------------


def generate_approval_number(session: Session) -> str:
    """Generate a unique human-readable approval number (APR-XXXXXXXX).

    Uses a random 8-char hex suffix.  Uniqueness is enforced by the DB
    unique index on ``approval_request.approval_number``.
    """
    import random
    import string

    while True:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        candidate = f"APR-{suffix}"
        existing = session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.approval_number == candidate,
            )
        ).scalar_one_or_none()
        if existing is None:
            return candidate


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_approval_request_by_number(
    session: Session,
    approval_number: str,
) -> ApprovalRequest:
    """Return an ``ApprovalRequest`` by its human-readable approval_number.

    Raises:
        ApprovalRequestNotFoundError: no matching row.
    """
    request = session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.approval_number == approval_number,
        )
    ).scalar_one_or_none()
    if request is None:
        raise ApprovalRequestNotFoundError(
            uuid.uuid5(uuid.NAMESPACE_URL, approval_number)
        )
    return request


def get_pending_request(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
) -> ApprovalRequest | None:
    """Return the active PENDING request for an entity, or ``None``."""
    return session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.entity_type == entity_type,
            ApprovalRequest.entity_id == entity_id,
            ApprovalRequest.status == "PENDING",
        )
    ).scalar_one_or_none()


def get_approval_request(
    session: Session,
    request_id: uuid.UUID,
) -> ApprovalRequest:
    """Return a single ``ApprovalRequest`` by ID.

    Raises:
        ApprovalRequestNotFoundError: no matching row.
    """
    return _get_request(session, request_id)


def list_pending_requests(
    session: Session,
    *,
    assigned_approver_id: uuid.UUID | None = None,
) -> list[ApprovalRequest]:
    """Return all PENDING approval requests, optionally filtered by
    ``assigned_approver_id``.
    """
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.status == "PENDING",
    )
    if assigned_approver_id is not None:
        stmt = stmt.where(
            ApprovalRequest.assigned_approver_id == assigned_approver_id,
        )
    stmt = stmt.order_by(ApprovalRequest.requested_at.asc())
    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_request(
    session: Session, request_id: uuid.UUID
) -> ApprovalRequest:
    """Return an ``ApprovalRequest`` or raise."""
    request = session.get(ApprovalRequest, request_id)
    if request is None:
        raise ApprovalRequestNotFoundError(request_id)
    return request


__all__ = [
    "ApprovalRequestAlreadyExistsError",
    "ApprovalRequestNotFoundError",
    "InvalidApprovalTransitionError",
    "SeparationOfDutiesError",
    "approve_request",
    "cancel_request",
    "create_approval_request",
    "generate_approval_number",
    "get_approval_request",
    "get_approval_request_by_number",
    "get_pending_request",
    "list_pending_requests",
    "reject_request",
]
