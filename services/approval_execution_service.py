"""Service layer for executing deferred bot commands after approval.

Per ADR-008 §6, approval_required=True commands store their execution
data as a JSON ``payload`` in the ``approval_request`` row.  When an
approver grants approval, this service retrieves the payload and
dispatches to the appropriate command executor.

Design:
    This module does NOT perform the approval itself -- that is
    ``approval_service.approve_request()``.  This module only runs the
    deferred mutation *after* approval has been recorded.

Authorization:
    This module does NOT check permissions -- the approval itself
    already implies authorization.  This module only executes the
    deferred mutation.

Known limitation:
    Double-execution of an already-resolved request is prevented by the
    approval_service's transition guard (APPROVED is terminal), but
    under extreme concurrency two approve calls could race.  Documented,
    not solved with a new idempotency framework per explicit instruction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from sqlalchemy.orm import Session

from database.models.approval_request import ApprovalRequest
from services import approval_service, audit_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ApprovalNotApprovedError(ValueError):
    """Raised when attempting to execute a request that is not APPROVED."""

    def __init__(self, request_id: uuid.UUID, status: str) -> None:
        super().__init__(
            f"Approval request '{request_id}' is '{status}', not 'APPROVED'."
        )
        self.request_id = request_id
        self.status = status


class UnknownCommandTypeError(ValueError):
    """Raised when the entity_type does not match a known executor."""

    def __init__(self, entity_type: str) -> None:
        super().__init__(
            f"No executor registered for entity_type '{entity_type}'."
        )
        self.entity_type = entity_type


class PayloadMissingError(ValueError):
    """Raised when the approval request has no payload."""

    def __init__(self, request_id: uuid.UUID) -> None:
        super().__init__(
            f"Approval request '{request_id}' has no payload to execute."
        )
        self.request_id = request_id


# ---------------------------------------------------------------------------
# Executor registry
# ---------------------------------------------------------------------------


#: Maps command name -> executor function.
#: Each executor receives (session, payload_dict, actor_user_id)
#: and returns a result string.
EXECUTOR_REGISTRY: dict[str, Callable[..., str]] = {}


def _register_executor(command_name: str):  # type: ignore[no-untyped-def]
    """Decorator to register a command executor."""

    def decorator(func):  # type: ignore[no-untyped-def]
        EXECUTOR_REGISTRY[command_name] = func
        return func

    return decorator


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------


def execute_approved_request(
    session: Session,
    *,
    request_id: uuid.UUID,
    approver_id: uuid.UUID,
) -> str:
    """Execute the deferred mutation for an approved request.

    Flow:
        1. Load the approval request by ID.
        2. Validate it is APPROVED.
        3. Extract the command name from ``entity_type``.
        4. Look up the executor in ``EXECUTOR_REGISTRY``.
        5. Call the executor with the payload.
        6. Record an audit entry for the execution.

    Args:
        session: Active database session.
        request_id: ID of the approved approval request.
        approver_id: ID of the user who approved (for audit).

    Returns:
        A human-readable result string.

    Raises:
        ApprovalRequestNotFoundError: request does not exist.
        ApprovalNotApprovedError: request is not in APPROVED status.
        PayloadMissingError: request has no payload.
        UnknownCommandTypeError: no executor for this entity_type.
    """
    request = approval_service.get_approval_request(session, request_id)

    if request.status != "APPROVED":
        raise ApprovalNotApprovedError(request_id, request.status)

    if request.payload is None:
        raise PayloadMissingError(request_id)

    # Extract command name from entity_type (e.g. "bot_command:create-order")
    entity_type = request.entity_type
    if not entity_type.startswith("bot_command:"):
        raise UnknownCommandTypeError(entity_type)

    command_name = entity_type[len("bot_command:"):]

    executor = EXECUTOR_REGISTRY.get(command_name)
    if executor is None:
        raise UnknownCommandTypeError(entity_type)

    # Execute the deferred mutation.
    result = executor(session, request.payload, approver_id)

    # Audit the execution.
    audit_service.record(
        session,
        entity_type="approval_request",
        entity_id=request.id,
        action="UPDATE",
        actor_user_id=approver_id,
        before={"status": "APPROVED", "executed": False},
        after={"status": "APPROVED", "executed": True, "result": result},
    )
    session.flush()

    return result


__all__ = [
    "ApprovalNotApprovedError",
    "EXECUTOR_REGISTRY",
    "PayloadMissingError",
    "UnknownCommandTypeError",
    "execute_approved_request",
]
