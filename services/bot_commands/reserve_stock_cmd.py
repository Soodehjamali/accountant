"""``/reserve-stock`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/reserve-stock`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Stock
reservation is the automatic step after order approval — it checks
inventory availability and either reserves or backorders.

Domain interpretation:
    ``/reserve-stock`` triggers stock reservation on an ``Order`` (T10)
    that is in ``APPROVED`` state.  Via the canonical
    ``order_service.reserve_order_stock()``, the system:
    - Checks available stock for every order line
    - If sufficient: creates ``StockReservation`` rows, transitions
      to ``RESERVED``
    - If insufficient: transitions to ``BACKORDERED``

Command syntax:
    /reserve-stock <order_number>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
    - Order must be in APPROVED state
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
    """Parse /reserve-stock arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /reserve-stock <order_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /reserve-stock <order_number>\n"
            "Example: /reserve-stock ORD-20260827-A1B2C3D4"
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

    # 3. Validate order is in APPROVED state.
    if order.state != "APPROVED":
        return (
            f"Order '{order_number}' is in state '{order.state}' "
            f"and cannot reserve stock. Only APPROVED orders can "
            f"reserve stock."
        )

    # 4. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_reserve_stock(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the stock reservation (APPROVED → RESERVED/BACKORDERED).

    Called directly by the command handler since /reserve-stock is Tier 2
    (no approval required).

    Uses the canonical ``order_service.reserve_order_stock()``.

    The result may be RESERVED (stock available) or BACKORDERED
    (insufficient stock) — both are valid outcomes.
    """
    from services import order_service

    order_id = uuid.UUID(payload["order_id"])

    order = order_service.reserve_order_stock(
        session, order_id, actor_user_id=actor_user_id,
    )

    return (
        f"Order {payload['order_number']} stock reservation complete.\n"
        f"  Status: {order.state}"
    )
