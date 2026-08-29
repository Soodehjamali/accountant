"""``/order-history`` bot command handler (Tier 1 — read-only, BOT_QUERY).

Allows a representative to view the state-transition history of their
own order.  Uses the existing persisted ``order_status_history`` (T12)
as the source of truth via ``order_service.get_order_history()``.

Command syntax:
    /order-history <order_number>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)

Authorization:
    - BOT_QUERY required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
"""

from __future__ import annotations

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.order import Order
from database.models.order_status_history import OrderStatusHistory
from database.models.representative import Representative


def get_order_history_display(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> str:
    """Parse /order-history arguments, validate scope, format history.

    Returns a formatted string on success, or an error string on failure.

    Syntax: /order-history <order_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /order-history <order_number>\n"
            "Example: /order-history ORD-20260827-A1B2C3D4"
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

    # 3. Fetch persisted history from order_status_history (source of truth).
    history_records = list(
        session.execute(
            sa_select(OrderStatusHistory)
            .where(OrderStatusHistory.order_id == order.id)
            .order_by(OrderStatusHistory.event_at)
        ).scalars().all()
    )

    # 4. Format the response.
    lines = [
        f"Order History: {order.order_number}",
        f"Current Status: {order.state}",
        "",
    ]

    if not history_records:
        lines.append("No history records found.")
    else:
        for i, h in enumerate(history_records, 1):
            lines.append(f"{i}. {h.from_state} -> {h.to_state}")

            # Resolve actor display name.
            actor_name = "system"
            if h.actor_user_id:
                actor = session.get(AppUser, h.actor_user_id)
                if actor:
                    actor_name = (
                        getattr(actor, "username", None)
                        or getattr(actor, "email", None)
                        or "user"
                    )

            date_str = (
                h.event_at.strftime("%Y-%m-%d %H:%M")
                if h.event_at else "???"
            )
            lines.append(f"   Actor: {actor_name}")
            lines.append(f"   Date: {date_str}")
            if h.note:
                lines.append(f"   Note: {h.note}")
            lines.append("")

    return "\n".join(lines).rstrip()
