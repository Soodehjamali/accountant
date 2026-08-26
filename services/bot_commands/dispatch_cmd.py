"""``/dispatch`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/dispatch`` is a Tier 2 command: requires ``BOT_WRITE``
but does NOT require an approval request.

Domain interpretation:
    ``/dispatch`` dispatches a ``StockTransfer`` (T4) that is in
    ``DRAFT`` state and originates from the representative's warehouse.
    On dispatch, the canonical ``stock_transfer_service.dispatch_transfer()``
    transitions the transfer to ``DISPATCHED`` and posts the ``TRANSFER_OUT``
    inventory transaction.

Command syntax:
    /dispatch <transfer_number>

    - transfer_number: the business transfer number (e.g. TRF-20260826-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Transfer must be in DRAFT state
    - Source warehouse must be assigned to the representative
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
    """Parse /dispatch arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /dispatch <transfer_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /dispatch <transfer_number>\n"
            "Example: /dispatch TRF-20260826-A1B2C3D4"
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

    # 3. Validate transfer is in DRAFT state.
    if transfer.state != "DRAFT":
        return (
            f"Transfer '{transfer_number}' is in state '{transfer.state}' "
            f"and cannot be dispatched. Only DRAFT transfers can be "
            f"dispatched."
        )

    # 4. Validate source warehouse belongs to the representative.
    has_assignment = session.execute(
        sa_select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == rep.id,
            WarehouseAssignment.warehouse_id == transfer.source_warehouse_id,
        )
    ).scalar_one_or_none()
    if has_assignment is None:
        return (
            f"Transfer '{transfer_number}' does not originate from a "
            f"warehouse assigned to you. Access denied."
        )

    # 5. Build payload for execution.
    payload = {
        "transfer_id": str(transfer.id),
        "transfer_number": transfer.transfer_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_dispatch(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the transfer dispatch.

    Called directly by the command handler since /dispatch is Tier 2
    (no approval required).

    Uses the canonical ``stock_transfer_service.dispatch_transfer()``.
    """
    from services import stock_transfer_service

    transfer_id = uuid.UUID(payload["transfer_id"])

    transfer = stock_transfer_service.dispatch_transfer(
        session, transfer_id, actor_user_id=actor_user_id,
        note=f"Dispatched by bot user",
    )

    return (
        f"Transfer {payload['transfer_number']} dispatched successfully.\n"
        f"  Status: {transfer.state}"
    )
