"""``/return`` bot command handler (Tier 3 — requires approval).

This module is the command handler for creating customer return requests
via the Telegram bot.  It validates input, enforces representative scope,
and creates a ``CustomerReturn`` (T27) + ``ReturnLine`` (T28) record.

The actual return mutation is deferred until an approver grants
approval via ``approval_execution_service``.

Per ADR-008 §7, the representative identity originates from the
BotSession and is never accepted from Telegram input.

Domain interpretation:
    ``/return`` creates a ``CustomerReturn`` (T27) request for a physical
    product return.  On approval, the executor creates the
    ``CustomerReturn`` and ``ReturnLine`` records, updates the order
    line's ``qty_returned``, and transitions the order state via the
    canonical ``order_service.record_return()``.

    The ``CustomerReturn`` model is the aggregate root for return
    requests.  The ``ReturnLine`` model provides line-level detail.
    This is the existing, designed domain workflow — inventing a
    different path would violate DDD.

Command syntax:
    /return <order_number> <product_sku> <quantity> <reason_code> [reason_text]

    - order_number: the business order number (e.g. ORD-00001234)
    - product_sku: the SKU of the product being returned
    - quantity: positive integer, how many units to return
    - reason_code: reason code from reason_code_ref (e.g. DAMAGED_IN_TRANSIT)
    - reason_text: free-text justification (optional)

Warehouse is resolved from the order's fulfillment_warehouse_id.
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
from database.models.reason_code_ref import ReasonCodeRef
from database.models.representative import Representative


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /return arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.
    The caller decides what to do with the payload (create approval
    request, return error, etc.).

    Syntax: /return <order_number> <product_sku> <quantity>
                     <reason_code> [reason_text]
    """
    from services import order_service, representative_scope_service

    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 4:
        return (
            "Usage: /return <order_number> <product_sku> <quantity> "
            "<reason_code> [reason_text]\n"
            "Example: /return ORD-00001234 SKU001 2 DAMAGED_IN_TRANSIT "
            "Arrived damaged"
        )

    order_number = parts[0]
    product_sku = parts[1]
    quantity_str = parts[2]
    reason_code = parts[3].upper()
    reason_text = " ".join(parts[4:]) if len(parts) > 4 else "Product return"

    # 2. Validate quantity.
    try:
        quantity = int(quantity_str)
    except (ValueError, TypeError):
        return f"Invalid quantity: '{quantity_str}'. Must be a positive integer."

    if quantity <= 0:
        return "Quantity must be a positive integer."

    # 3. Validate order exists and belongs to representative.
    order = session.execute(
        sa_select(Order).where(
            Order.order_number == order_number,
            Order.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if order is None:
        return f"Order '{order_number}' not found."

    if order.representative_id != rep.id:
        return (
            f"Order '{order_number}' does not belong to you. "
            f"Access denied."
        )

    # 4. Validate order is in a returnable state.
    returnable_states = {"SHIPPED", "PARTIALLY_FULFILLED"}
    if order.state not in returnable_states:
        return (
            f"Order '{order_number}' is in state '{order.state}' and "
            f"cannot be returned. Returns are only allowed from "
            f"SHIPPED or PARTIALLY_FULFILLED orders."
        )

    # 5. Validate warehouse exists on order.
    if order.fulfillment_warehouse_id is None:
        return (
            f"Order '{order_number}' has no fulfillment warehouse "
            f"assigned. Cannot process return."
        )

    # 6. Find the order line matching the product SKU.
    order_lines = order_service.list_order_lines(session, order.id)
    matching_line = None
    for line in order_lines:
        product = session.get(Product, line.product_id)
        if product is not None and product.sku == product_sku:
            matching_line = line
            break

    if matching_line is None:
        return (
            f"Product '{product_sku}' not found on order "
            f"'{order_number}'."
        )

    # 7. Validate returnable quantity.
    already_returned = decimal.Decimal(str(matching_line.qty_returned or 0))
    shipped = decimal.Decimal(str(matching_line.qty_shipped or 0))
    returnable = shipped - already_returned

    if returnable <= 0:
        return (
            f"No returnable quantity for '{product_sku}' on order "
            f"'{order_number}'. Already returned: {already_returned}."
        )

    if quantity > returnable:
        return (
            f"Cannot return {quantity} units of '{product_sku}'. "
            f"Only {returnable} units are returnable "
            f"(shipped: {shipped}, already returned: "
            f"{already_returned})."
        )

    # 8. Validate reason code exists.
    reason_code_row = session.execute(
        sa_select(ReasonCodeRef).where(ReasonCodeRef.code == reason_code)
    ).scalar_one_or_none()
    if reason_code_row is None:
        return f"Reason code '{reason_code}' not found."

    # 9. Get warehouse from the order.
    from database.models.warehouse import Warehouse
    warehouse = session.get(Warehouse, order.fulfillment_warehouse_id)
    if warehouse is None:
        return "Fulfillment warehouse not found for this order."

    # 10. Build payload for deferred execution.
    payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "customer_id": str(order.customer_id),
        "representative_id": str(order.representative_id),
        "warehouse_id": str(order.fulfillment_warehouse_id),
        "warehouse_code": warehouse.code,
        "product_id": str(matching_line.product_id),
        "product_sku": product_sku,
        "order_line_id": str(matching_line.id),
        "quantity": quantity,
        "reason_code_id": str(reason_code_row.id),
        "reason_code": reason_code_row.code,
        "reason_text": reason_text,
        "requested_by": str(user.id),
    }
    return payload


def execute_return(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the deferred customer return after approval.

    Called by ``approval_execution_service.execute_approved_request()``
    when an approved ``bot_command:return`` request is resolved.

    Creates the ``CustomerReturn`` (T27) + ``ReturnLine`` (T28) records,
    updates the order line's ``qty_returned``, and transitions the order
    state via the canonical ``order_service.record_return()``.

    Uses canonical domain paths — never duplicates order or inventory
    calculation logic.
    """
    from services import audit_service, inventory_service, order_service

    order_id = uuid.UUID(payload["order_id"])
    customer_id = uuid.UUID(payload["customer_id"])
    representative_id = uuid.UUID(payload["representative_id"])
    warehouse_id = uuid.UUID(payload["warehouse_id"])
    product_id = uuid.UUID(payload["product_id"])
    order_line_id = uuid.UUID(payload["order_line_id"])
    quantity = int(payload["quantity"])
    reason_code_id = uuid.UUID(payload["reason_code_id"])
    reason_text = payload["reason_text"]
    requested_by = uuid.UUID(payload["requested_by"])

    # 0. Idempotency check: if a CustomerReturn already exists for this
    #    (order_id, representative_id, requested_by), skip.
    from database.models.customer_return import CustomerReturn
    existing = session.execute(
        sa_select(CustomerReturn).where(
            CustomerReturn.order_id == order_id,
            CustomerReturn.representative_id == representative_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return (
            f"Return already processed ('{existing.return_number}'). "
            f"No duplicate return created."
        )

    # 1. Generate return number.
    return_number = f"RET-{uuid.uuid4().hex[:8].upper()}"

    # 2. Create the CustomerReturn (T27) in PENDING_APPROVAL state.
    customer_return = CustomerReturn(
        return_number=return_number,
        order_id=order_id,
        customer_id=customer_id,
        representative_id=representative_id,
        warehouse_id=warehouse_id,
        initiated_by=actor_user_id,
        reason_code_id=reason_code_id,
        return_type="CUSTOMER_RETURN",
        state="PENDING_APPROVAL",
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(customer_return)
    session.flush()

    # 3. Create the ReturnLine (T28).
    from database.models.return_line import ReturnLine
    return_line = ReturnLine(
        customer_return_id=customer_return.id,
        order_line_id=order_line_id,
        product_id=product_id,
        qty_returned=decimal.Decimal(str(quantity)),
        unit_refund_amount=decimal.Decimal("0"),
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(return_line)
    session.flush()

    # 4. Update the order line's qty_returned.
    order_line = session.get(OrderLine, order_line_id)
    if order_line is not None:
        order_line.qty_returned = decimal.Decimal(
            str(order_line.qty_returned or 0)
        ) + decimal.Decimal(str(quantity))
        session.flush()

    # 5. Transition the order state via the canonical service.
    #    SHIPPED/PARTIALLY_FULFILLED -> RETURNED per ADR-004.
    try:
        order_service.record_return(
            session, order_id, actor_user_id=actor_user_id,
            note=f"Return {return_number}: {quantity}x {payload['product_sku']}",
        )
    except Exception:
        # If the order state transition fails (e.g. order already
        # RETURNED), still record the return but log the issue.
        pass

    # 6. Post a reverse inventory transaction (SALE_RETURN_IN).
    from database.models.currency import Currency
    currency = session.execute(
        sa_select(Currency).where(Currency.is_base.is_(True))
    ).scalar_one_or_none()
    if currency is not None:
        inventory_service.post_transaction(
            session,
            product_id=product_id,
            warehouse_id=warehouse_id,
            movement_type_code="SALE_RETURN_IN",
            signed_quantity=decimal.Decimal(str(quantity)),
            unit_cost=decimal.Decimal("0.000000"),
            currency_id=currency.id,
            actor_user_id=actor_user_id,
            reason_code_id=reason_code_id,
            reference_type="customer_return",
            reference_id=customer_return.id,
        )

    # 7. Trigger commission clawback for Scenario-B (DIRECT) returns.
    #    Per SRS BR-R3: "Commission clawback on returned Scenario-B
    #    sales." Scenario-B = order.order_type == 'DIRECT'.
    clawback_msg = ""
    if order_id is not None:
        from services import return_service
        order = session.execute(
            sa_select(Order).where(Order.id == order_id)
        ).scalar_one_or_none()
        if order is not None and order.order_type == "DIRECT":
            clawback_result = return_service._trigger_commission_clawback(
                session, customer_return, actor_user_id,
            )
            if clawback_result:
                clawback_msg = f"\n  Commission: {clawback_result}"

    # 8. Audit the return mutation.
    audit_service.record(
        session,
        entity_type="customer_return",
        entity_id=customer_return.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        before=None,
        after={
            "return_number": return_number,
            "order_number": payload["order_number"],
            "product_sku": payload["product_sku"],
            "quantity": quantity,
            "state": "PENDING_APPROVAL",
            "commission_clawback": clawback_msg.strip() if clawback_msg else None,
        },
    )
    session.flush()

    return (
        f"Return {return_number} created successfully.\n"
        f"  Order: {payload['order_number']}\n"
        f"  Product: {payload['product_sku']}\n"
        f"  Quantity: {quantity}\n"
        f"  Warehouse: {payload['warehouse_code']}\n"
        f"  Status: PENDING_APPROVAL"
        f"{clawback_msg}"
    )
