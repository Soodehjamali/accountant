"""``/create-invoice`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/create-invoice`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Creating an
invoice from a shipped order is the representative's standard workflow
step — the order has already been fully validated and shipped.

Domain interpretation:
    ``/create-invoice`` creates a new ``DRAFT`` invoice (T17) from a
    shipped ``Order`` (T10) via the canonical
    ``invoice_service.create_invoice_from_order()``.  The invoice copies
    order lines, computes totals, and links to the order via
    ``invoice_order`` (J1).  The invoice starts in DRAFT and must be
    explicitly issued via ``/issue-invoice``.

Command syntax:
    /create-invoice <order_number>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
    - Order must be in SHIPPED state
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
    """Parse /create-invoice arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /create-invoice <order_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /create-invoice <order_number>\n"
            "Example: /create-invoice ORD-20260827-A1B2C3D4"
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

    # 3. Validate order is in SHIPPED state.
    if order.state != "SHIPPED":
        return (
            f"Order '{order_number}' is in state '{order.state}' "
            f"and cannot create an invoice. Only SHIPPED orders can "
            f"be invoiced."
        )

    # 4. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_create_invoice(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the invoice creation from a shipped order.

    Called directly by the command handler since /create-invoice is Tier 2
    (no approval required).

    Uses the canonical ``invoice_service.create_invoice_from_order()``.
    """
    from services import invoice_service

    order_id = uuid.UUID(payload["order_id"])

    invoice = invoice_service.create_invoice_from_order(
        session, order_id=order_id, created_by=actor_user_id,
    )

    return (
        f"Invoice {invoice.invoice_number} created from order "
        f"{payload['order_number']}.\n"
        f"  Status: {invoice.state}\n"
        f"  Total: {invoice.grand_total}"
    )
