"""``/confirm`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/confirm`` is a Tier 2 command: requires ``BOT_WRITE``
but does NOT require an approval request.  A representative confirming
receipt of their own stock is a bounded write within their scope.

Domain interpretation:
    ``/confirm`` confirms receipt of a ``StockTransfer`` (T4) that is in
    ``DISPATCHED`` state and destined for the representative's warehouse.
    On confirmation, the canonical ``stock_transfer_service.receive_transfer()``
    transitions the transfer to ``RECEIVED`` and posts the ``TRANSFER_IN``
    inventory transaction.

Command syntax:
    /confirm <transfer_number>

    - transfer_number: the business transfer number (e.g. TRF-20260826-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Transfer must be in DISPATCHED state
    - Destination warehouse must be assigned to the representative
"""

from __future__ import annotations

import uuid

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
    """Parse /confirm arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /confirm <transfer_number>
    """
    from services import representative_scope_service, stock_transfer_service

    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /confirm <transfer_number>\n"
            "Example: /confirm TRF-20260826-A1B2C3D4"
        )

    transfer_number = parts[0]

    # 2. Validate transfer exists.
    transfer = session.execute(
        sa_select(StockTransfer).where(
            StockTransfer.transfer_number == transfer_number,
            StockTransfer.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if transfer is None:
        return f"Transfer '{transfer_number}' not found."

    # 3. Validate transfer is in DISPATCHED state.
    if transfer.state != "DISPATCHED":
        return (
            f"Transfer '{transfer_number}' is in state '{transfer.state}' "
            f"and cannot be confirmed. Only DISPATCHED transfers can be "
            f"confirmed."
        )

    # 4. Validate destination warehouse belongs to the representative.
    #    The representative must have an active warehouse assignment for the
    #    destination warehouse.
    has_assignment = session.execute(
        sa_select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == rep.id,
            WarehouseAssignment.warehouse_id == transfer.destination_warehouse_id,
        )
    ).scalar_one_or_none()
    if has_assignment is None:
        return (
            f"Transfer '{transfer_number}' is not destined for a warehouse "
            f"assigned to you. Access denied."
        )

    # 5. Build payload for execution.
    payload = {
        "transfer_id": str(transfer.id),
        "transfer_number": transfer.transfer_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_confirm(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the transfer confirmation (receive).

    Called directly by the command handler since /confirm is Tier 2
    (no approval required).

    Uses the canonical ``stock_transfer_service.receive_transfer()``.
    """
    from services import stock_transfer_service

    transfer_id = uuid.UUID(payload["transfer_id"])

    transfer = stock_transfer_service.receive_transfer(
        session, transfer_id, actor_user_id=actor_user_id,
        note=f"Confirmed by bot user",
    )

    return (
        f"Transfer {payload['transfer_number']} confirmed successfully.\n"
        f"  Status: {transfer.state}"
    )
