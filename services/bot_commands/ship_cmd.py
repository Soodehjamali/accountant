"""``/ship`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/ship`` is a Tier 2 command: requires ``BOT_WRITE``
but does NOT require an approval request.  Recording a shipment is the
representative indicating that goods for their own FULFILLING order have
been picked, packed, and dispatched.

Domain interpretation:
    ``/ship`` records a shipment against an ``Order`` (T10) that is in
    ``FULFILLING`` or ``PARTIALLY_FULFILLED`` state.  On shipment, the
    canonical ``order_service.ship_order()`` posts an inventory-ledger
    ``SALE_OUT`` row per shipped line (skipped for DIRECT orders),
    consumes the line's ``ACTIVE`` reservation, updates ``qty_shipped``,
    and transitions to ``SHIPPED`` (if every line is fully shipped) or
    ``PARTIALLY_FULFILLED``.

Command syntax:
    /ship <order_number> <product_sku> <quantity>

    - order_number: the business order number (e.g. ORD-20260827-XXXXXXXX)
    - product_sku: the product SKU (e.g. SKU-ABC12345)
    - quantity: the quantity to ship (e.g. 10 or 5.5)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Order must belong to the representative (scoped lookup)
    - Order must be in FULFILLING or PARTIALLY_FULFILLED state
"""

from __future__ import annotations

import decimal
import uuid

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.product import Product
from database.models.representative import Representative


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /ship arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /ship <order_number> <product_sku> <quantity>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 3:
        return (
            "Usage: /ship <order_number> <product_sku> <quantity>\n"
            "Example: /ship ORD-20260827-A1B2C3D4 SKU-ABC12345 10"
        )

    order_number = parts[0]
    product_sku = parts[1]
    quantity_str = parts[2]

    # 2. Parse quantity early for user-friendly error.
    try:
        quantity = decimal.Decimal(quantity_str)
    except (decimal.InvalidOperation, ValueError):
        return f"Invalid quantity: '{quantity_str}'. Please provide a valid number."

    if quantity <= 0:
        return f"Quantity must be greater than zero. Got: {quantity}"

    # 3. Validate order exists and belongs to the representative.
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

    # 4. Validate order is in a shippable state.
    if order.state not in ("FULFILLING", "PARTIALLY_FULFILLED"):
        return (
            f"Order '{order_number}' is in state '{order.state}' "
            f"and cannot be shipped. Only FULFILLING or "
            f"PARTIALLY_FULFILLED orders can be shipped."
        )

    # 5. Resolve product by SKU.
    product = session.execute(
        sa_select(Product).where(
            Product.sku == product_sku,
            Product.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if product is None:
        return f"Product '{product_sku}' not found."

    # 6. Find the order line matching this product on this order.
    #    Unique constraint: (order_id, product_id, lot_id) — at most one
    #    line per product per order (with a given lot).
    order_line = session.execute(
        sa_select(OrderLine).where(
            OrderLine.order_id == order.id,
            OrderLine.product_id == product.id,
        )
    ).scalar_one_or_none()
    if order_line is None:
        return (
            f"Product '{product_sku}' is not on order '{order_number}'."
        )

    # 7. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "order_line_id": str(order_line.id),
        "product_sku": product.sku,
        "quantity": str(quantity),
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_ship(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the shipment recording.

    Called directly by the command handler since /ship is Tier 2
    (no approval required).

    Uses the canonical ``order_service.ship_order()``.

    Catches domain exceptions and formats user-friendly messages
    without leaking UUIDs or internal details.
    """
    from services import order_service

    order_id = uuid.UUID(payload["order_id"])
    order_line_id = uuid.UUID(payload["order_line_id"])
    quantity = decimal.Decimal(payload["quantity"])

    try:
        order = order_service.ship_order(
            session,
            order_id,
            shipments=[
                order_service.ShipmentInput(
                    order_line_id=order_line_id,
                    quantity=quantity,
                ),
            ],
            actor_user_id=actor_user_id,
        )
    except order_service.InvalidOrderStateTransitionError as exc:
        return f"Error: {exc}"
    except order_service.ShipmentQuantityError as exc:
        # The domain error includes the order_line UUID — format cleanly.
        return (
            f"Cannot ship {quantity} of {payload['product_sku']}: "
            f"only {exc.remaining} remains unshipped."
        )

    return (
        f"Shipment recorded for {payload['product_sku']} "
        f"({quantity}) on order {payload['order_number']}.\n"
        f"  Status: {order.state}"
    )
