"""``/backorder-resubmit`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/backorder-resubmit`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Resubmitting
a BACKORDERED order is the representative's manual retry path — the
only way out of BACKORDERED besides cancellation (per ADR-004 point 3:
"No automatic background retry job").

Domain interpretation:
    ``/backorder-resubmit`` transitions an ``Order`` (T10) from
    ``BACKORDERED`` to ``PENDING_APPROVAL`` via the canonical
    ``order_service.resubmit_order()`` which calls the ``_transition()``
    choke point, writing the matching ``order_status_history`` (T12)
    and ``audit_log`` (H6) rows.

Command syntax:
    /backorder-resubmit <order_number>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
    - Order must be in BACKORDERED state
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
    """Parse /backorder-resubmit arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /backorder-resubmit <order_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /backorder-resubmit <order_number>\n"
            "Example: /backorder-resubmit ORD-20260827-A1B2C3D4"
        )

    order_number = parts[0]

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

    # 3. Validate order is in BACKORDERED state.
    if order.state != "BACKORDERED":
        return (
            f"Order '{order_number}' is in state '{order.state}' "
            f"and cannot be resubmitted. Only BACKORDERED orders can be "
            f"resubmitted for approval."
        )

    # 4. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_backorder_resubmit(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the order resubmission (BACKORDERED → PENDING_APPROVAL).

    Called directly by the command handler since /backorder-resubmit is
    Tier 2 (no approval required).

    Uses the canonical ``order_service.resubmit_order()``.
    """
    from services import order_service

    order_id = uuid.UUID(payload["order_id"])

    order = order_service.resubmit_order(
        session, order_id, actor_user_id=actor_user_id,
    )

    return (
        f"Order {payload['order_number']} resubmitted for approval.\n"
        f"  Status: {order.state}"
    )
