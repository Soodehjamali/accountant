"""``/create-order`` bot command handler (Tier 3 — requires approval).

This module is the command handler for creating sales orders via the
Telegram bot.  It validates input, enforces representative scope,
and creates an approval request with the order data as payload.

The actual order creation is deferred until an approver grants
approval via ``approval_execution_service``.

Per ADR-008 §7, the representative identity originates from the
BotSession and is never accepted from Telegram input.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.customer import Customer
from database.models.price_history import PriceHistory
from database.models.product import Product
from database.models.representative import Representative
from database.models.warehouse import Warehouse


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /create-order arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.
    The caller decides what to do with the payload (create approval
    request, return error, etc.).

    Syntax: /create-order <customer_code> <product_sku> <qty> [fulfillment_mode]
    """
    from services import representative_scope_service

    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 3:
        return (
            "Usage: /create-order <customer_code> <product_sku> <qty> "
            "[fulfillment_mode]\n"
            "Example: /create-order CUST001 SKU001 10 REP_LOCAL"
        )

    customer_code = parts[0]
    product_sku = parts[1]
    try:
        qty = int(parts[2])
        if qty <= 0:
            return "Quantity must be a positive integer."
    except ValueError:
        return f"Invalid quantity: '{parts[2]}'. Must be a positive integer."

    fulfillment_mode = parts[3] if len(parts) > 3 else "REP_LOCAL"
    if fulfillment_mode not in ("REP_LOCAL", "FACTORY_DIRECT"):
        return (
            f"Invalid fulfillment mode: '{fulfillment_mode}'. "
            f"Use REP_LOCAL or FACTORY_DIRECT."
        )

    # 2. Validate customer is in representative scope.
    customers = representative_scope_service.resolve_representative_customers(
        session, rep.id,
    )
    customer = None
    for c in customers:
        if c.code == customer_code:
            customer = c
            break
    if customer is None:
        return (
            f"Customer '{customer_code}' is not assigned to you. "
            f"Use /customers to see your assigned customers."
        )

    # 3. Validate product exists and is active.
    product = session.execute(
        sa_select(Product).where(
            Product.sku == product_sku,
            Product.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if product is None:
        return f"Product '{product_sku}' not found or inactive."

    # 4. Validate warehouse is in representative scope.
    warehouses = representative_scope_service.resolve_representative_warehouses(
        session, rep.id, primary_only=True,
    )
    if not warehouses:
        return "No warehouse assigned to you. Cannot create order."
    warehouse = warehouses[0]

    # 5. Resolve price for this product via its price list.
    from services import price_list_service

    price_entry = session.execute(
        sa_select(PriceHistory).where(
            PriceHistory.product_id == product.id,
            PriceHistory.effective_from <= datetime.now(timezone.utc),
            (
                PriceHistory.effective_to.is_(None)
                | (PriceHistory.effective_to > datetime.now(timezone.utc))
            ),
        ).order_by(PriceHistory.effective_from.desc())
    ).scalar_one_or_none()
    if price_entry is None:
        return f"No active price found for product '{product_sku}'."

    # 6. Build payload for deferred execution.
    payload = {
        "customer_id": str(customer.id),
        "customer_code": customer.code,
        "product_id": str(product.id),
        "product_sku": product.sku,
        "qty": qty,
        "fulfillment_mode": fulfillment_mode,
        "warehouse_id": str(warehouse.id),
        "warehouse_code": warehouse.code,
        "price_history_id": str(price_entry.id),
        "price_list_id": str(price_entry.price_list_id),
        "currency_id": str(customer.currency_id),
        "representative_id": str(rep.id),
        "sales_channel": "BOT_TELEGRAM",
        "order_type": "LOCAL",
    }
    return payload


def execute_create_order(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the deferred order creation after approval.

    Called by ``approval_execution_service.execute_approved_request()``
    when an approved ``bot_command:create-order`` request is resolved.

    Uses the canonical ``order_service.create_order()`` — never
    duplicates order creation logic.
    """
    from services import order_service
    from services.order_service import OrderLineInput

    customer_id = uuid.UUID(payload["customer_id"])
    product_id = uuid.UUID(payload["product_id"])
    qty = int(payload["qty"])
    fulfillment_mode = payload["fulfillment_mode"]
    warehouse_id = uuid.UUID(payload["warehouse_id"])
    price_history_id = uuid.UUID(payload["price_history_id"])
    price_list_id = uuid.UUID(payload["price_list_id"])
    currency_id = uuid.UUID(payload["currency_id"])
    representative_id = uuid.UUID(payload["representative_id"])
    sales_channel = payload["sales_channel"]
    order_type = payload["order_type"]

    # Create the order via the canonical service path.
    order = order_service.create_order(
        session,
        customer_id=customer_id,
        representative_id=representative_id,
        currency_id=currency_id,
        price_list_id=price_list_id,
        order_type=order_type,
        fulfillment_mode=fulfillment_mode,
        sales_channel=sales_channel,
        lines=[
            OrderLineInput(
                product_id=product_id,
                fulfillment_warehouse_id=warehouse_id,
                price_history_id=price_history_id,
                qty_ordered=qty,
                fulfillment_mode=fulfillment_mode,
            ),
        ],
        created_by=actor_user_id,
    )

    return (
        f"Order {order.order_number} created successfully.\n"
        f"  State: {order.state}\n"
        f"  Total: {order.grand_total}"
    )
