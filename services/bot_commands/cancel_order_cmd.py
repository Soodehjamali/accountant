"""``/cancel-order`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/cancel-order`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Cancelling an
order is the representative cancelling their *own* order — the domain
service owns all business rules.

Domain interpretation:
    ``/cancel-order`` cancels an ``Order`` (T10) that is in a cancellable
    state (DRAFT, PENDING_APPROVAL, APPROVED, RESERVED, BACKORDERED,
    or FULFILLING — per ``order_service._CANCELLABLE_STATES``).  On
    cancellation, the canonical ``order_service.cancel_order()`` releases
    any ACTIVE ``StockReservation`` rows and transitions the order to
    ``CANCELLED`` via the ``_transition()`` choke point, writing the
    matching ``order_status_history`` (T12) and ``audit_log`` (H6) rows.

Command syntax:
    /cancel-order <order_number>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
    - Order must be in a cancellable state
"""

from __future__ import annotations

import uuid

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.order import Order
from database.models.representative import Representative


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /cancel-order arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /cancel-order <order_number> [reason]
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /cancel-order <order_number>\n"
            "Example: /cancel-order ORD-20260827-A1B2C3D4"
        )

    order_number = parts[0]
    reason = " ".join(parts[1:]) if len(parts) > 1 else None

    # 2. Validate order exists and belongs to the representative.
    #    Single authorization-aware query: order_number + representative_id
    #    in one WHERE clause to prevent IDOR and existence leakage.
    order = session.execute(
        sa_select(Order).where(
            Order.order_number == order_number,
            Order.representative_id == rep.id,
            Order.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if order is None:
        return f"Order '{order_number}' not found."

    # 3. Validate order is in a cancellable state.
    #    Use the same _CANCELLABLE_STATES from order_service so the bot
    #    never invents its own cancellation rules.
    from services.order_service import _CANCELLABLE_STATES, OrderNotCancellableError

    if order.state not in _CANCELLABLE_STATES:
        return (
            f"Order '{order_number}' is in state '{order.state}' "
            f"and cannot be cancelled."
        )

    # 4. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
        "note": reason,
    }
    return payload


def execute_cancel_order(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the order cancellation.

    Called directly by the command handler since /cancel-order is Tier 2
    (no approval required).

    Uses the canonical ``order_service.cancel_order()`` which:
    - Releases any ACTIVE StockReservation rows
    - Transitions to CANCELLED via _transition()
    - Writes order_status_history and audit_log
    """
    from services import order_service

    order_id = uuid.UUID(payload["order_id"])
    note = payload.get("note")

    order = order_service.cancel_order(
        session, order_id, actor_user_id=actor_user_id, note=note,
    )

    return (
        f"Order {payload['order_number']} cancelled successfully.\n"
        f"  Status: {order.state}"
    )
