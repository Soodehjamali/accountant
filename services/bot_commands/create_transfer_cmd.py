"""``/create-transfer`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/create-transfer`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Creating a
stock transfer is bounded by warehouse scope — the representative
must have both source and destination warehouses assigned.

Domain interpretation:
    ``/create-transfer`` creates a new ``DRAFT`` stock transfer (T4)
    with one or more transfer lines via the canonical
    ``stock_transfer_service.create_transfer()``.  The transfer
    starts in DRAFT and must be explicitly dispatched via ``/dispatch``.

    This is a DRAFT-only creation — no inventory impact occurs until
    dispatch time (per ADR-005's two-phase model).

Command syntax:
    /create-transfer <source_code> <dest_code> <product_sku> <qty> [unit_cost]

    - source_code: source warehouse code (e.g. WH-MAIN)
    - dest_code: destination warehouse code (e.g. WH-BRANCH)
    - product_sku: product SKU (e.g. SKU-001)
    - qty: quantity to transfer (positive decimal)
    - unit_cost: optional unit cost (defaults to 0)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Both source and destination warehouses must be assigned to the
      representative (warehouse scope)
    - Product must exist
"""

from __future__ import annotations

import decimal
import uuid

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.product import Product
from database.models.representative import Representative
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /create-transfer arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /create-transfer <source_code> <dest_code> <product_sku> <qty> [unit_cost]
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 4:
        return (
            "Usage: /create-transfer <source_code> <dest_code> <product_sku> <qty> [unit_cost]\n"
            "Example: /create-transfer WH-MAIN WH-BRANCH SKU-001 10\n"
            "Example: /create-transfer WH-MAIN WH-BRANCH SKU-001 10 25000"
        )

    source_code = parts[0]
    dest_code = parts[1]
    product_sku = parts[2]
    qty_str = parts[3]
    unit_cost_str = parts[4] if len(parts) > 4 else "0"

    # 2. Validate quantity.
    try:
        qty = decimal.Decimal(qty_str)
    except (decimal.InvalidOperation, ValueError):
        return f"Invalid quantity: '{qty_str}'. Must be a positive number."

    if qty <= 0:
        return "Quantity must be positive."

    # 3. Validate unit cost.
    try:
        unit_cost = decimal.Decimal(unit_cost_str)
    except (decimal.InvalidOperation, ValueError):
        return f"Invalid unit cost: '{unit_cost_str}'. Must be a non-negative number."

    if unit_cost < 0:
        return "Unit cost must be non-negative."

    # 4. Validate source warehouse exists and is in scope.
    source_wh = session.execute(
        sa_select(Warehouse).where(
            Warehouse.code == source_code,
            Warehouse.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if source_wh is None:
        return f"Warehouse '{source_code}' not found."

    has_source_assignment = session.execute(
        sa_select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == rep.id,
            WarehouseAssignment.warehouse_id == source_wh.id,
        )
    ).scalar_one_or_none()
    if has_source_assignment is None:
        return f"Warehouse '{source_code}' is not assigned to you."

    # 5. Validate destination warehouse exists and is in scope.
    dest_wh = session.execute(
        sa_select(Warehouse).where(
            Warehouse.code == dest_code,
            Warehouse.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if dest_wh is None:
        return f"Warehouse '{dest_code}' not found."

    has_dest_assignment = session.execute(
        sa_select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == rep.id,
            WarehouseAssignment.warehouse_id == dest_wh.id,
        )
    ).scalar_one_or_none()
    if has_dest_assignment is None:
        return f"Warehouse '{dest_code}' is not assigned to you."

    # 6. Validate source != destination.
    if source_wh.id == dest_wh.id:
        return "Source and destination warehouses must be different."

    # 7. Validate product exists.
    product = session.execute(
        sa_select(Product).where(
            Product.sku == product_sku,
            Product.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if product is None:
        return f"Product '{product_sku}' not found."

    # 8. Build payload for execution.
    payload = {
        "source_warehouse_id": str(source_wh.id),
        "source_warehouse_code": source_wh.code,
        "destination_warehouse_id": str(dest_wh.id),
        "destination_warehouse_code": dest_wh.code,
        "product_id": str(product.id),
        "product_sku": product.sku,
        "qty_requested": str(qty),
        "unit_cost": str(unit_cost),
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_create_transfer(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the transfer creation.

    Called directly by the command handler since /create-transfer is Tier 2
    (no approval required).

    Uses the canonical ``stock_transfer_service.create_transfer()``.
    """
    from services import stock_transfer_service

    transfer = stock_transfer_service.create_transfer(
        session,
        source_warehouse_id=uuid.UUID(payload["source_warehouse_id"]),
        destination_warehouse_id=uuid.UUID(payload["destination_warehouse_id"]),
        lines=[
            stock_transfer_service.TransferLineInput(
                product_id=uuid.UUID(payload["product_id"]),
                qty_requested=decimal.Decimal(payload["qty_requested"]),
                unit_cost=decimal.Decimal(payload["unit_cost"]),
            ),
        ],
        requested_by=actor_user_id,
    )

    return (
        f"Transfer {transfer.transfer_number} created successfully.\n"
        f"  From: {payload['source_warehouse_code']}\n"
        f"  To: {payload['destination_warehouse_code']}\n"
        f"  Product: {payload['product_sku']}\n"
        f"  Qty: {payload['qty_requested']}\n"
        f"  Status: {transfer.state}"
    )
