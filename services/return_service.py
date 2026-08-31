"""Service layer for the Customer Return aggregate (``customer_return`` T27 /
``return_line`` T28).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.

**Business rules implemented:**
    BR-R3: Commission clawback on returned Scenario-B sales.
    Scenario-B = ``order.order_type == 'DIRECT'`` (factory ships to
    customer; rep earns commission).  When a return referencing a
    DIRECT order is closed, the originating commission transaction
    (ACCRUED or APPROVED) is clawed back.

    The RETURNED state transition for the originating order is handled
    by the existing ``order_service.record_return()`` -- this module
    focuses on the return lifecycle and financial side effects
    (commission clawback, eventual credit note).

**Return state machine:**
    PENDING_APPROVAL → APPROVED → RECEIVED → INSPECTED → CLOSED
                                                   ↘ REJECTED
    Approval is handled by the generic ``approval_request`` pair
    (entity_type='customer_return').

**Idempotency:**
    ``close_return`` checks whether a CLAWED_BACK commission already
    exists for the return's order before attempting clawback, preventing
    duplicate reversals from retries.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.customer_return import CustomerReturn
from database.models.order import Order
from services import audit_service, commission_service


# --- Allowed state transitions for the return lifecycle ---
_RETURN_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING_APPROVAL": frozenset({"APPROVED", "REJECTED"}),
    "APPROVED": frozenset({"RECEIVED", "CANCELLED"}),
    "RECEIVED": frozenset({"INSPECTED", "CANCELLED"}),
    "INSPECTED": frozenset({"CLOSED", "REJECTED"}),
    "CLOSED": frozenset(),
    "REJECTED": frozenset(),
}


class ReturnNotFoundError(LookupError):
    """Raised when a referenced ``return_id`` has no matching row."""

    def __init__(self, return_id: uuid.UUID) -> None:
        super().__init__(f"No customer return with id '{return_id}' exists.")
        self.return_id = return_id


class InvalidReturnStateTransitionError(ValueError):
    """Raised when a transition is not a valid edge in the return state machine."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(
            f"Cannot transition a return from '{from_state}' to '{to_state}'."
        )
        self.from_state = from_state
        self.to_state = to_state


class ReturnAlreadyClosedError(ValueError):
    """Raised when attempting to close an already-closed return."""

    def __init__(self, return_id: uuid.UUID) -> None:
        super().__init__(f"Return '{return_id}' is already closed.")
        self.return_id = return_id


def _get_return_or_raise(session: Session, return_id: uuid.UUID) -> CustomerReturn:
    """Return a ``CustomerReturn`` by ID, or raise ``ReturnNotFoundError``."""
    cr = session.get(CustomerReturn, return_id)
    if cr is None:
        raise ReturnNotFoundError(return_id)
    return cr


def _transition_return(
    session: Session,
    customer_return: CustomerReturn,
    to_state: str,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CustomerReturn:
    """Validate and apply a return state transition.

    Raises:
        InvalidReturnStateTransitionError: if the edge is not allowed.
    """
    from_state = customer_return.state
    allowed = _RETURN_ALLOWED_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise InvalidReturnStateTransitionError(from_state, to_state)

    customer_return.state = to_state
    customer_return.updated_by = actor_user_id

    # Set timestamps for terminal states.
    now = datetime.datetime.now(datetime.timezone.utc)
    if to_state == "CLOSED":
        customer_return.closed_at = now
    elif to_state == "RECEIVED":
        customer_return.received_at = now

    audit_service.record(
        session,
        entity_type="customer_return",
        entity_id=customer_return.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": from_state},
        after={"state": to_state, "note": note},
    )
    session.flush()
    return customer_return


def _has_existing_clawback(session: Session, order_id: uuid.UUID) -> bool:
    """Return True if a CLAWED_BACK commission transaction already exists
    for this order, preventing duplicate clawbacks on retry."""
    from database.models.commission_transaction import CommissionTransaction

    existing = session.execute(
        select(CommissionTransaction).where(
            CommissionTransaction.order_id == order_id,
            CommissionTransaction.state_event == "CLAWED_BACK",
        )
    ).scalar_one_or_none()
    return existing is not None


def _trigger_commission_clawback(
    session: Session,
    customer_return: CustomerReturn,
    actor_user_id: uuid.UUID,
) -> str | None:
    """Clawback commission for Scenario-B (DIRECT) order returns.

    Per SRS BR-R3: "Commission clawback on returned Scenario-B sales."

    Scenario-B is identified by ``order.order_type == 'DIRECT'``
    (factory ships to customer; rep receives commission).

    Returns a human-readable result string, or None if no clawback
    was needed/possible.
    """
    if customer_return.order_id is None:
        return None

    order = session.execute(
        select(Order).where(Order.id == customer_return.order_id)
    ).scalar_one_or_none()
    if order is None:
        return None

    # Commission clawback only applies to Scenario-B (DIRECT) orders.
    # Scenario-A (LOCAL) orders may or may not have commission per policy,
    # but BR-R3 explicitly scopes clawback to Scenario-B.
    if order.order_type != "DIRECT":
        return None

    # Idempotency: skip if clawback already exists for this order.
    if _has_existing_clawback(session, order.id):
        return "Commission clawback already recorded for this order."

    # Find the original ACCRUED or APPROVED commission transaction.
    from database.models.commission_transaction import CommissionTransaction

    original_txn = session.execute(
        select(CommissionTransaction).where(
            CommissionTransaction.order_id == order.id,
            CommissionTransaction.state_event.in_(["ACCRUED", "APPROVED"]),
        )
        .order_by(CommissionTransaction.occurred_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if original_txn is None:
        return None

    # Perform the clawback via the canonical commission service.
    clawback_txn = commission_service.clawback_commission(
        session,
        original_txn.id,
        actor_user_id=actor_user_id,
        note=f"Commission clawback for return {customer_return.return_number}",
    )

    return (
        f"Commission clawback recorded: {clawback_txn.signed_amount} "
        f"(original: {original_txn.signed_amount})"
    )


def close_return(
    session: Session,
    return_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CustomerReturn:
    """Close a return and trigger financial side effects.

    Transitions the return from INSPECTED → CLOSED and, per the DB
    spec, triggers commission clawback for Scenario-B (DIRECT) order
    returns.

    Per ERD T27: "closing posts a SALE_RETURN_IN inventory_transaction
    and, where applicable, a CLAWED_BACK commission_transaction row
    and a credit_note."

    The SALE_RETURN_IN inventory transaction is posted by the existing
    ``execute_return`` bot command (or the equivalent API entry point)
    at return creation time.  Commission clawback is triggered here
    at close time.

    Credit note generation is deferred to a future financial milestone
    (not yet implemented).

    Args:
        session: Active SQLAlchemy session (caller commits).
        return_id: The customer return to close.
        actor_user_id: User performing the close.
        note: Optional note for the audit trail.

    Raises:
        ReturnNotFoundError: no matching return.
        InvalidReturnStateTransitionError: return is not in INSPECTED.
    """
    customer_return = _get_return_or_raise(session, return_id)
    _transition_return(
        session,
        customer_return,
        "CLOSED",
        actor_user_id=actor_user_id,
        note=note,
    )

    # Trigger commission clawback for Scenario-B (DIRECT) returns.
    clawback_result = _trigger_commission_clawback(
        session, customer_return, actor_user_id,
    )

    if clawback_result:
        audit_service.record(
            session,
            entity_type="customer_return",
            entity_id=customer_return.id,
            action="UPDATE",
            actor_user_id=actor_user_id,
            after={"clawback_result": clawback_result},
        )
        session.flush()

    return customer_return


def receive_return(
    session: Session,
    return_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CustomerReturn:
    """Transition a return: APPROVED → RECEIVED (physical receipt at warehouse)."""
    customer_return = _get_return_or_raise(session, return_id)
    return _transition_return(
        session,
        customer_return,
        "RECEIVED",
        actor_user_id=actor_user_id,
        note=note,
    )


def inspect_return(
    session: Session,
    return_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CustomerReturn:
    """Transition a return: RECEIVED → INSPECTED (warehouse inspection complete)."""
    customer_return = _get_return_or_raise(session, return_id)
    return _transition_return(
        session,
        customer_return,
        "INSPECTED",
        actor_user_id=actor_user_id,
        note=note,
    )


def get_return(session: Session, return_id: uuid.UUID) -> CustomerReturn:
    """Return a single ``CustomerReturn`` by ID.

    Raises:
        ReturnNotFoundError: no matching row.
    """
    return _get_return_or_raise(session, return_id)


__all__ = [
    "ReturnAlreadyClosedError",
    "ReturnNotFoundError",
    "InvalidReturnStateTransitionError",
    "close_return",
    "get_return",
    "inspect_return",
    "receive_return",
]
