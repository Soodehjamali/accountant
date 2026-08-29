"""``/transfers`` bot command handler (Tier 1 — read-only, BOT_QUERY).

Lists stock transfers visible to the currently authenticated representative.
A transfer is visible when:
    - source_warehouse is assigned to the representative (OUTBOUND), or
    - destination_warehouse is assigned to the representative (INBOUND)

Scope enforcement happens in the service layer, not in this handler.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.representative import Representative


def list_visible_transfers(
    session: Session,
    *,
    representative_id,
    limit: int = 10,
):
    """Return transfers visible to the representative.

    Returns a list of dicts with keys:
        transfer_number, direction, source_code, dest_code, state, requested_at

    Scope enforcement: only transfers whose source OR destination warehouse
    is assigned to the representative are returned.
    """
    import uuid as _uuid
    from sqlalchemy import select as sa_select, or_

    from database.models.stock_transfer import StockTransfer
    from database.models.warehouse import Warehouse
    from database.models.warehouse_assignment import WarehouseAssignment
    from services.representative_scope_service import resolve_representative_warehouses

    # Get the representative's assigned warehouse IDs (scope).
    warehouses = resolve_representative_warehouses(session, representative_id)
    wh_ids = [w.id for w in warehouses]
    if not wh_ids:
        return []

    # Single query: transfers where source OR destination is in scope.
    # Use subqueries to avoid loading warehouse objects.
    from sqlalchemy import select

    stmt = (
        select(StockTransfer)
        .where(
            StockTransfer.deleted_at.is_(None),
            or_(
                StockTransfer.source_warehouse_id.in_(wh_ids),
                StockTransfer.destination_warehouse_id.in_(wh_ids),
            ),
        )
        .order_by(StockTransfer.requested_at.desc())
        .limit(limit)
    )
    transfers = list(session.execute(stmt).scalars().all())

    # Build results with warehouse codes resolved.
    wh_map = {w.id: w.code for w in warehouses}
    results = []
    for t in transfers:
        # Resolve warehouse codes (may need to load warehouses not in the
        # representative's own assignments, e.g. the "other" side).
        src_code = wh_map.get(t.source_warehouse_id)
        if src_code is None:
            wh = session.get(Warehouse, t.source_warehouse_id)
            src_code = wh.code if wh else "???"

        dst_code = wh_map.get(t.destination_warehouse_id)
        if dst_code is None:
            wh = session.get(Warehouse, t.destination_warehouse_id)
            dst_code = wh.code if wh else "???"

        # Determine direction from the representative's perspective.
        if t.source_warehouse_id in wh_ids:
            direction = "OUTBOUND"
        else:
            direction = "INBOUND"

        results.append({
            "transfer_number": t.transfer_number,
            "direction": direction,
            "source_code": src_code,
            "dest_code": dst_code,
            "state": t.state,
            "requested_at": t.requested_at,
        })

    return results


def get_visible_transfer(
    session: Session,
    *,
    representative_id,
    transfer_number: str,
):
    """Return a single transfer visible to the representative, or None.

    The query combines transfer_number lookup with warehouse scope
    in a single authorization-aware query to prevent IDOR.

    Returns a dict with transfer detail data, or None if not found/unauthorized.
    """
    from sqlalchemy import select, or_

    from database.models.stock_transfer import StockTransfer
    from database.models.transfer_line import TransferLine
    from database.models.warehouse import Warehouse
    from database.models.product import Product
    from services.representative_scope_service import resolve_representative_warehouses

    # Get the representative's assigned warehouse IDs (scope).
    warehouses = resolve_representative_warehouses(session, representative_id)
    wh_ids = [w.id for w in warehouses]
    if not wh_ids:
        return None

    # Authorization-aware query: transfer_number + scope in one WHERE clause.
    stmt = select(StockTransfer).where(
        StockTransfer.transfer_number == transfer_number,
        StockTransfer.deleted_at.is_(None),
        or_(
            StockTransfer.source_warehouse_id.in_(wh_ids),
            StockTransfer.destination_warehouse_id.in_(wh_ids),
        ),
    )
    transfer = session.execute(stmt).scalar_one_or_none()
    if transfer is None:
        return None

    # Resolve warehouse codes.
    wh_map = {w.id: w.code for w in warehouses}
    src_code = wh_map.get(transfer.source_warehouse_id)
    if src_code is None:
        wh = session.get(Warehouse, transfer.source_warehouse_id)
        src_code = wh.code if wh else "???"
    dst_code = wh_map.get(transfer.destination_warehouse_id)
    if dst_code is None:
        wh = session.get(Warehouse, transfer.destination_warehouse_id)
        dst_code = wh.code if wh else "???"

    direction = "OUTBOUND" if transfer.source_warehouse_id in wh_ids else "INBOUND"

    # Load transfer lines with products.
    lines_stmt = (
        select(TransferLine)
        .where(TransferLine.stock_transfer_id == transfer.id)
        .order_by(TransferLine.created_at)
    )
    transfer_lines = list(session.execute(lines_stmt).scalars().all())

    line_details = []
    for tl in transfer_lines:
        product = session.get(Product, tl.product_id)
        product_name = product.sku if product else "???"
        line_details.append({
            "product": product_name,
            "qty_requested": tl.qty_requested,
            "qty_dispatched": tl.qty_dispatched,
            "qty_received": tl.qty_received,
        })

    return {
        "transfer_number": transfer.transfer_number,
        "direction": direction,
        "source_code": src_code,
        "dest_code": dst_code,
        "state": transfer.state,
        "requested_at": transfer.requested_at,
        "dispatched_at": transfer.dispatched_at,
        "received_at": transfer.received_at,
        "lines": line_details,
    }


def get_visible_transfer_history(
    session: Session,
    *,
    representative_id,
    transfer_number: str,
):
    """Return persisted transfer history records for a visible transfer, or None.

    The query combines transfer_number lookup with warehouse scope
    in a single authorization-aware query to prevent IDOR.

    Returns a dict with transfer history data, or None if not found/unauthorized.
    """
    from sqlalchemy import select, or_

    from database.models.stock_transfer import StockTransfer
    from database.models.transfer_history import TransferHistory
    from database.models.app_user import AppUser
    from services.representative_scope_service import resolve_representative_warehouses

    # Get the representative's assigned warehouse IDs (scope).
    warehouses = resolve_representative_warehouses(session, representative_id)
    wh_ids = [w.id for w in warehouses]
    if not wh_ids:
        return None

    # Authorization-aware query: transfer_number + scope in one WHERE clause.
    stmt = select(StockTransfer).where(
        StockTransfer.transfer_number == transfer_number,
        StockTransfer.deleted_at.is_(None),
        or_(
            StockTransfer.source_warehouse_id.in_(wh_ids),
            StockTransfer.destination_warehouse_id.in_(wh_ids),
        ),
    )
    transfer = session.execute(stmt).scalar_one_or_none()
    if transfer is None:
        return None

    # Determine direction.
    direction = "OUTBOUND" if transfer.source_warehouse_id in wh_ids else "INBOUND"

    # Query persisted history records (source of truth).
    history_stmt = (
        select(TransferHistory)
        .where(TransferHistory.stock_transfer_id == transfer.id)
        .order_by(TransferHistory.event_at, TransferHistory.id)
    )
    history_records = list(session.execute(history_stmt).scalars().all())

    # Resolve actor display names.
    entries = []
    for h in history_records:
        actor_name = "system"
        if h.actor_user_id:
            actor = session.get(AppUser, h.actor_user_id)
            if actor:
                actor_name = getattr(actor, "username", None) or getattr(actor, "email", None) or "user"
        entries.append({
            "from_state": h.from_state,
            "to_state": h.to_state,
            "actor": actor_name,
            "event_at": h.event_at,
            "note": h.note,
        })

    return {
        "transfer_number": transfer.transfer_number,
        "direction": direction,
        "state": transfer.state,
        "history": entries,
    }
