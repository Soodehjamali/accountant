"""``/cancel-transfer`` bot command handler (Tier 2 — direct write, no approval).

Cancels a DRAFT stock transfer originating from the representative's
source warehouse.  Uses the canonical
``stock_transfer_service.cancel_transfer()``.

Scope enforcement: only the source-side representative may cancel.
"""

from __future__ import annotations

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.representative import Representative
from database.models.stock_transfer import StockTransfer
from database.models.warehouse_assignment import WarehouseAssignment


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /cancel-transfer arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /cancel-transfer <transfer_number>
    """
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /cancel-transfer <transfer_number>\n"
            "Example: /cancel-transfer TRF-20260826-A1B2C3D4"
        )

    transfer_number = parts[0]

    # Validate transfer exists.
    transfer = session.execute(
        sa_select(StockTransfer).where(
            StockTransfer.transfer_number == transfer_number,
            StockTransfer.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if transfer is None:
        return f"Transfer '{transfer_number}' not found."

    # Scope: source warehouse must be assigned to the representative.
    has_assignment = session.execute(
        sa_select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == rep.id,
            WarehouseAssignment.warehouse_id == transfer.source_warehouse_id,
        )
    ).scalar_one_or_none()
    if has_assignment is None:
        return f"Transfer '{transfer_number}' not found."

    # Validate state (domain service also validates, but early feedback is nicer).
    if transfer.state != "DRAFT":
        return (
            f"Transfer '{transfer_number}' is in state '{transfer.state}' "
            f"and cannot be cancelled. Only DRAFT transfers can be cancelled."
        )

    return {
        "transfer_id": str(transfer.id),
        "transfer_number": transfer.transfer_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }


def execute_cancel_transfer(
    session: Session,
    payload: dict,
    actor_user_id,
) -> str:
    """Execute the transfer cancellation."""
    import uuid as _uuid
    from services import stock_transfer_service

    transfer_id = _uuid.UUID(payload["transfer_id"])

    transfer = stock_transfer_service.cancel_transfer(
        session, transfer_id, actor_user_id=actor_user_id,
        note="Cancelled by bot user",
    )

    return (
        f"Transfer {payload['transfer_number']} cancelled successfully.\n"
        f"  Status: {transfer.state}"
    )
