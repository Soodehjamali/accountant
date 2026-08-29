"""``/mark-completed`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/mark-completed`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Completing
a paid order is the representative's standard workflow step that
finalizes the order lifecycle.

Domain interpretation:
    ``/mark-completed`` transitions an ``Order`` (T10) from
    ``PAID`` to ``COMPLETED`` via the canonical
    ``order_service.mark_completed()`` which calls the ``_transition()``
    choke point, writing the matching ``order_status_history`` (T12)
    and ``audit_log`` (H6) rows, and automatically calculates the
    commission transaction for this order via
    ``commission_service.calculate_commission_for_order()``.

    Commission auto-calculation is best-effort: if no commission config
    matches or it was already calculated, the order still completes
    successfully.

Command syntax:
    /mark-completed <order_number>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
    - Order must be in PAID state
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
    """Parse /mark-completed arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /mark-completed <order_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /mark-completed <order_number>\n"
            "Example: /mark-completed ORD-20260827-A1B2C3D4"
        )

    order_number = parts[0]

    # 2. Validate order exists and belongs to the representative.
    #    Single authorization-aware query to prevent IDOR.
    order = session.execute(
        sa_select(Order).where(
            Order.order_number == order_number,
            Order.representative_id == rep.id,
            Order.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if order is None:
        return f"Order '{order_number}' not found."

    # 3. Validate order is in PAID state.
    if order.state != "PAID":
        return (
            f"Order '{order_number}' is in state '{order.state}' "
            f"and cannot be completed. Only PAID orders can "
            f"be completed."
        )

    # 4. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_mark_completed(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the order completion (PAID → COMPLETED).

    Called directly by the command handler since /mark-completed is Tier 2
    (no approval required).

    Uses the canonical ``order_service.mark_completed()`` which also
    auto-calculates commission (best-effort).
    """
    from services import order_service

    order_id = uuid.UUID(payload["order_id"])

    order = order_service.mark_completed(
        session, order_id, actor_user_id=actor_user_id,
    )

    return (
        f"Order {payload['order_number']} completed successfully.\n"
        f"  Status: {order.state}"
    )
