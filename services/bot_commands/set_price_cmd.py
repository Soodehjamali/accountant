"""``/set-price`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/set-price`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Per
``04_Business_Policies.md``: *\"Representative may change selling
price.  Price change affects only current invoice.\"*

Domain interpretation:
    ``/set-price`` overrides the selling price (``unit_price``) on
    an order line of the representative's current DRAFT order.  The
    override affects only the current order — it does not persist to
    the ``price_history`` ledger.

    This is bounded by order scope: the representative can only
    modify lines on their own DRAFT orders.  Once the order passes
    APPROVED, the price is frozen.

Command syntax:
    /set-price <product_sku> <price>

    - product_sku: product SKU (e.g. SKU-001)
    - price: new unit price (positive decimal, e.g. 25000)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Only the representative's own DRAFT orders can be modified
    - Product must exist on the DRAFT order
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
    """Parse /set-price arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /set-price <product_sku> <price>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 2:
        return (
            "Usage: /set-price <product_sku> <price>\n"
            "Example: /set-price SKU-001 25000"
        )

    product_sku = parts[0]
    price_str = parts[1]

    # 2. Validate price.
    try:
        price = decimal.Decimal(price_str)
    except (decimal.InvalidOperation, ValueError):
        return f"Invalid price: '{price_str}'. Must be a non-negative number."

    if price < 0:
        return "Price must be non-negative."

    # 3. Validate product exists.
    product = session.execute(
        sa_select(Product).where(
            Product.sku == product_sku,
            Product.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if product is None:
        return f"Product '{product_sku}' not found."

    # 4. Find the representative's latest DRAFT order.
    from services import order_service

    order = order_service.get_latest_draft_order_for_representative(
        session, rep.id,
    )
    if order is None:
        return (
            "No DRAFT order found. "
            "Create an order first, then use /set-price to adjust pricing."
        )

    # 5. Find the order line for this product.
    line = session.execute(
        sa_select(OrderLine).where(
            OrderLine.order_id == order.id,
            OrderLine.product_id == product.id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        return (
            f"Product '{product_sku}' is not on order '{order.order_number}'. "
            "Add it to the order first."
        )

    # 6. Build payload for execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "order_line_id": str(line.id),
        "product_id": str(product.id),
        "product_sku": product.sku,
        "old_unit_price": str(line.unit_price),
        "new_unit_price": str(price),
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_set_price(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the price override.

    Called directly by the command handler since /set-price is Tier 2
    (no approval required).

    Uses the canonical ``order_service.update_order_line_price()``.
    """
    from services import order_service

    order_id = uuid.UUID(payload["order_id"])

    order_service.update_order_line_price(
        session,
        order_id=order_id,
        order_line_id=uuid.UUID(payload["order_line_id"]),
        new_unit_price=decimal.Decimal(payload["new_unit_price"]),
        actor_user_id=actor_user_id,
    )

    # Reload the order to get the updated grand_total.
    order = order_service.get_order(session, order_id)

    return (
        f"Price updated on order {payload['order_number']}.\n"
        f"  Product: {payload['product_sku']}\n"
        f"  Old price: {payload['old_unit_price']}\n"
        f"  New price: {payload['new_unit_price']}\n"
        f"  New order total: {order.grand_total}"
    )
