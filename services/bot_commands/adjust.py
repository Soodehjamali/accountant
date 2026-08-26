"""``/adjust`` bot command handler (Tier 3 — requires approval).

This module is the command handler for creating stock adjustment requests
via the Telegram bot.  It validates input, enforces representative scope,
and creates a ``StockAdjustment`` (T7) record with PENDING state.

The actual inventory mutation is deferred until an approver grants
approval via ``approval_execution_service``.

Per ADR-008 §7, the representative identity originates from the
BotSession and is never accepted from Telegram input.

Domain interpretation:
    ``/adjust`` creates a ``StockAdjustment`` (T7) request for manual
    correction / damage / write-off.  On approval, the executor posts
    the corresponding ``InventoryTransaction`` (T1) to the inventory
    ledger via the canonical ``inventory_service.post_transaction()``.

    The ``StockAdjustment`` model is the aggregate root for adjustment
    requests.  It is the canonical domain concept for manual
    corrections — inventing a different path would violate DDD.

Command syntax:
    /adjust <product_sku> <adjustment_type> <delta_quantity>
            <reason_code> [reason_text]

    - product_sku: the SKU of the product to adjust
    - adjustment_type: POSITIVE | NEGATIVE | DAMAGE | WRITEOFF | STOCKTAKE
    - delta_quantity: signed quantity (positive for POSITIVE type,
      negative for others)
    - reason_code: reason code from reason_code_ref (e.g. PRICING_ERROR)
    - reason_text: free-text justification (optional, defaults to type)

Warehouse is automatically resolved from the representative's primary
warehouse assignment (per established business rule).
"""

from __future__ import annotations

import decimal
import uuid

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.product import Product
from database.models.reason_code_ref import ReasonCodeRef
from database.models.representative import Representative
from database.models.stock_adjustment import StockAdjustment

#: Valid adjustment types from the StockAdjustment model's CHECK constraint.
VALID_ADJUSTMENT_TYPES = frozenset({"POSITIVE", "NEGATIVE", "DAMAGE", "WRITEOFF", "STOCKTAKE"})


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /adjust arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.
    The caller decides what to do with the payload (create approval
    request, return error, etc.).

    Syntax: /adjust <product_sku> <adjustment_type> <delta_quantity>
                     <reason_code> [reason_text]
    """
    from services import inventory_service, representative_scope_service

    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 4:
        return (
            "Usage: /adjust <product_sku> <adjustment_type> <delta_quantity> "
            "<reason_code> [reason_text]\n"
            "Types: POSITIVE, NEGATIVE, DAMAGE, WRITEOFF, STOCKTAKE\n"
            "Example: /adjust SKU001 NEGATIVE -10 PRICING_ERROR Damaged in transit"
        )

    product_sku = parts[0]
    adjustment_type = parts[1].upper()
    quantity_str = parts[2]
    reason_code = parts[3].upper()
    reason_text = " ".join(parts[4:]) if len(parts) > 4 else f"Manual {adjustment_type.lower()} adjustment"

    # 2. Validate adjustment type.
    if adjustment_type not in VALID_ADJUSTMENT_TYPES:
        return (
            f"Invalid adjustment type: '{adjustment_type}'. "
            f"Valid types: {', '.join(sorted(VALID_ADJUSTMENT_TYPES))}"
        )

    # 3. Validate delta_quantity.
    try:
        delta_quantity = decimal.Decimal(quantity_str)
    except (decimal.InvalidOperation, ValueError):
        return f"Invalid quantity: '{quantity_str}'. Must be a decimal number."

    if delta_quantity == 0:
        return "Quantity must be nonzero."

    # Validate sign matches type convention.
    if adjustment_type == "POSITIVE" and delta_quantity < 0:
        return f"A POSITIVE adjustment must have a positive quantity (got {delta_quantity})."
    if adjustment_type != "POSITIVE" and delta_quantity > 0:
        return f"A {adjustment_type} adjustment must have a negative quantity (got {delta_quantity})."

    # 4. Validate product exists.
    product = session.execute(
        sa_select(Product).where(
            Product.sku == product_sku,
            Product.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if product is None:
        return f"Product '{product_sku}' not found or inactive."

    # 5. Validate reason code exists.
    reason_code_row = session.execute(
        sa_select(ReasonCodeRef).where(ReasonCodeRef.code == reason_code)
    ).scalar_one_or_none()
    if reason_code_row is None:
        return f"Reason code '{reason_code}' not found."

    # 6. Validate warehouse is in representative scope (primary warehouse).
    warehouses = representative_scope_service.resolve_representative_warehouses(
        session, rep.id, primary_only=True,
    )
    if not warehouses:
        return "No warehouse assigned to you. Cannot create adjustment."
    warehouse = warehouses[0]

    # 7. Check resulting balance would not go negative.
    current_balance = inventory_service.get_balance(
        session,
        warehouse_id=warehouse.id,
        product_id=product.id,
    )
    if current_balance + delta_quantity < 0:
        return (
            f"Insufficient stock: current balance is {current_balance}, "
            f"adjustment would result in {current_balance + delta_quantity}."
        )

    # 8. Build payload for deferred execution.
    payload = {
        "product_id": str(product.id),
        "product_sku": product.sku,
        "warehouse_id": str(warehouse.id),
        "warehouse_code": warehouse.code,
        "adjustment_type": adjustment_type,
        "delta_quantity": str(delta_quantity),
        "reason_code_id": str(reason_code_row.id),
        "reason_code": reason_code_row.code,
        "reason_text": reason_text,
        "requested_by": str(user.id),
        "representative_id": str(rep.id),
    }
    return payload


def execute_adjust(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the deferred stock adjustment after approval.

    Called by ``approval_execution_service.execute_approved_request()``
    when an approved ``bot_command:adjust`` request is resolved.

    Creates a ``StockAdjustment`` (T7) in PENDING state, then applies it
    by posting the canonical ``InventoryTransaction`` (T1) to the
    inventory ledger.

    Uses the canonical ``inventory_service.post_transaction()`` — never
    duplicates inventory calculation logic.
    """
    from services import audit_service, inventory_service

    product_id = uuid.UUID(payload["product_id"])
    warehouse_id = uuid.UUID(payload["warehouse_id"])
    adjustment_type = payload["adjustment_type"]
    delta_quantity = decimal.Decimal(payload["delta_quantity"])
    reason_code_id = uuid.UUID(payload["reason_code_id"])
    reason_text = payload["reason_text"]
    requested_by = uuid.UUID(payload["requested_by"])

    # 0. Idempotency check: if a StockAdjustment already exists for this
    #    (warehouse, product, requested_by) with APPLIED state, skip.
    existing = session.execute(
        sa_select(StockAdjustment).where(
            StockAdjustment.warehouse_id == warehouse_id,
            StockAdjustment.product_id == product_id,
            StockAdjustment.requested_by == requested_by,
            StockAdjustment.state == "APPLIED",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return (
            f"Adjustment already applied (\u2018{existing.adjustment_number}\u2019). "
            f"No duplicate inventory transaction created."
        )

    # 1. Generate adjustment number.
    adjustment_number = f"ADJ-{uuid.uuid4().hex[:8].upper()}"

    # 2. Determine movement type code from adjustment type.
    movement_type_code_map = {
        "POSITIVE": "ADJUSTMENT_POSITIVE",
        "NEGATIVE": "ADJUSTMENT_NEGATIVE",
        "DAMAGE": "DAMAGED_OUT",
        "WRITEOFF": "DAMAGED_OUT",
        "STOCKTAKE": "ADJUSTMENT_POSITIVE" if delta_quantity > 0 else "ADJUSTMENT_NEGATIVE",
    }
    movement_type_code = movement_type_code_map[adjustment_type]

    # 3. Get the default currency for the inventory transaction.
    from database.models.currency import Currency
    currency = session.execute(
        sa_select(Currency).where(Currency.is_base.is_(True))
    ).scalar_one_or_none()
    if currency is None:
        raise RuntimeError("No base currency configured.")

    # 4. Post the inventory transaction (canonical write path).
    transaction = inventory_service.post_transaction(
        session,
        product_id=product_id,
        warehouse_id=warehouse_id,
        movement_type_code=movement_type_code,
        signed_quantity=delta_quantity,
        unit_cost=decimal.Decimal("0.000000"),
        currency_id=currency.id,
        actor_user_id=actor_user_id,
        reason_code_id=reason_code_id,
        reference_type="stock_adjustment",
        reference_id=None,  # Will be set after StockAdjustment creation
    )

    # 5. Create the StockAdjustment record (T7) in APPLIED state.
    adjustment = StockAdjustment(
        adjustment_number=adjustment_number,
        warehouse_id=warehouse_id,
        product_id=product_id,
        requested_by=requested_by,
        approved_by=actor_user_id,
        reason_code_id=reason_code_id,
        adjustment_type=adjustment_type,
        delta_quantity=delta_quantity,
        state="APPLIED",
        reason_text=reason_text,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(adjustment)
    session.flush()

    # 6. Update the transaction's reference_id to point to the adjustment.
    transaction.reference_id = adjustment.id
    session.flush()

    # 7. Audit the inventory mutation.
    audit_service.record(
        session,
        entity_type="stock_adjustment",
        entity_id=adjustment.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": "PENDING"},
        after={
            "state": "APPLIED",
            "adjustment_type": adjustment_type,
            "delta_quantity": str(delta_quantity),
            "transaction_id": str(transaction.id),
        },
    )
    session.flush()

    return (
        f"Adjustment {adjustment_number} applied successfully.\n"
        f"  Product: {payload['product_sku']}\n"
        f"  Warehouse: {payload['warehouse_code']}\n"
        f"  Type: {adjustment_type}\n"
        f"  Quantity: {delta_quantity}\n"
        f"  Balance change: {delta_quantity}"
    )
