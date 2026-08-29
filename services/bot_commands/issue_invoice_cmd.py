"""``/issue-invoice`` bot command handler (Tier 2 — direct write, no approval).

Per ADR-008 §2, ``/issue-invoice`` is a Tier 2 command: requires
``BOT_WRITE`` but does NOT require an approval request.  Issuing a
DRAFT invoice is the representative's standard workflow step after
creating an invoice from a shipped order.

Domain interpretation:
    ``/issue-invoice`` transitions an ``Invoice`` (T17) from
    ``DRAFT`` to ``ISSUED`` via the canonical
    ``invoice_service.issue_invoice()``.  This sets ``issued_at``
    and ``due_at``, freezes the invoice per ADR-006, and coordinates
    with the Order domain by calling ``order_service.mark_invoiced()``
    on the related order (SHIPPED → INVOICED).

Command syntax:
    /issue-invoice <invoice_number>

    - invoice_number: the business invoice number (e.g. INV-20260827-XXXXXXXX)

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Invoice must be linked to an order belonging to the representative
    - Invoice must be in DRAFT state
"""

from __future__ import annotations

import uuid

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.invoice import Invoice
from database.models.invoice_order import InvoiceOrder
from database.models.order import Order
from database.models.representative import Representative


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /issue-invoice arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /issue-invoice <invoice_number>
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 1:
        return (
            "Usage: /issue-invoice <invoice_number>\n"
            "Example: /issue-invoice INV-20260827-A1B2C3D4"
        )

    invoice_number = parts[0]

    # 2. Validate invoice exists and is linked to an order belonging to the representative.
    #    Single authorization-aware query to prevent IDOR.
    invoice = session.execute(
        sa_select(Invoice).where(
            Invoice.invoice_number == invoice_number,
            Invoice.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if invoice is None:
        return f"Invoice '{invoice_number}' not found."

    # 3. Validate invoice is in DRAFT state.
    if invoice.state != "DRAFT":
        return (
            f"Invoice '{invoice_number}' is in state '{invoice.state}' "
            f"and cannot be issued. Only DRAFT invoices can be issued."
        )

    # 4. Validate invoice is linked to an order belonging to the representative.
    invoice_order_link = session.execute(
        sa_select(InvoiceOrder).where(InvoiceOrder.invoice_id == invoice.id)
    ).scalar_one_or_none()
    if invoice_order_link is None:
        return (
            f"Invoice '{invoice_number}' is not linked to any order. "
            f"Cannot verify representative scope."
        )

    order = session.execute(
        sa_select(Order).where(
            Order.id == invoice_order_link.order_id,
            Order.representative_id == rep.id,
            Order.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if order is None:
        return (
            f"Invoice '{invoice_number}' does not belong to you. "
            f"Access denied."
        )

    # 5. Build payload for execution.
    payload = {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_issue_invoice(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the invoice issuance (DRAFT → ISSUED).

    Called directly by the command handler since /issue-invoice is Tier 2
    (no approval required).

    Uses the canonical ``invoice_service.issue_invoice()`` which also
    coordinates with order_service.mark_invoiced() internally.
    """
    from services import customer_ledger_service, invoice_service

    invoice_id = uuid.UUID(payload["invoice_id"])

    invoice = invoice_service.issue_invoice(
        session,
        invoice_id,
        actor_user_id=actor_user_id,
        record_entry=customer_ledger_service.record_entry,
    )

    return (
        f"Invoice {invoice.invoice_number} issued successfully.\n"
        f"  Status: {invoice.state}\n"
        f"  Total: {invoice.grand_total}\n"
        f"  Due: {invoice.due_at.strftime('%Y-%m-%d') if invoice.due_at else 'N/A'}"
    )
