"""``/record-payment`` bot command handler (Tier 3 — requires approval).

This module is the command handler for recording customer payments against
invoices via the Telegram bot.  It validates input, enforces representative
scope, and creates an approval request with the payment data as payload.

The actual payment recording is deferred until an approver grants
approval via ``approval_execution_service``.

Per ADR-008 §7, the representative identity originates from the
BotSession and is never accepted from Telegram input.

Domain interpretation:
    ``/record-payment`` creates a ``Payment`` (T19) record that allocates
    to one or more ``Invoice`` (T17) records.  On approval, the executor
    calls ``payment_service.record_payment()`` which:
    - Creates the Payment row (append-only)
    - Creates PaymentAllocation rows
    - Updates each invoice's amount_paid / balance_due
    - Transitions invoice state as needed (ISSUED → PARTIALLY_PAID → PAID)
    - Records customer ledger entry (T22) with PAYMENT_RECEIVED

Command syntax:
    /record-payment <invoice_number> <amount> <method> [reference]

    - invoice_number: the business invoice number (e.g. INV-20260827-XXXXXXXX)
    - amount: the payment amount (positive decimal)
    - method: payment method (CASH, BANK_TRANSFER, CHECK, CARD)
    - reference: optional payment reference text

Authorization:
    - BOT_WRITE required
    - Representative identity from BotSession.representative_id
    - Invoice must be linked to an order belonging to the representative
    - Invoice must be in ISSUED or PARTIALLY_PAID state
"""

from __future__ import annotations

import decimal
import uuid

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.invoice import Invoice
from database.models.invoice_order import InvoiceOrder
from database.models.order import Order
from database.models.representative import Representative

#: Valid payment methods.
VALID_PAYMENT_METHODS = frozenset({"CASH", "BANK_TRANSFER", "CHECK", "CARD"})


def validate_and_build_payload(
    session: Session,
    *,
    rep: Representative,
    user: AppUser,
    args: str,
) -> dict | str:
    """Parse /record-payment arguments, validate scope, build payload.

    Returns a dict payload on success, or an error string on failure.

    Syntax: /record-payment <invoice_number> <amount> <method> [reference]
    """
    # 1. Parse arguments.
    parts = args.strip().split()
    if len(parts) < 3:
        return (
            "Usage: /record-payment <invoice_number> <amount> <method> [reference]\n"
            "Methods: CASH, BANK_TRANSFER, CHECK, CARD\n"
            "Example: /record-payment INV-20260827-A1B2C3D4 500000 BANK_TRANSFER REF-123"
        )

    invoice_number = parts[0]
    amount_str = parts[1]
    method = parts[2].upper()
    reference = parts[3] if len(parts) > 3 else None

    # 2. Validate amount.
    try:
        amount = decimal.Decimal(amount_str)
    except (decimal.InvalidOperation, ValueError):
        return f"Invalid amount: '{amount_str}'. Must be a positive number."

    if amount <= 0:
        return "Payment amount must be positive."

    # 3. Validate payment method.
    if method not in VALID_PAYMENT_METHODS:
        return (
            f"Invalid payment method: '{method}'. "
            f"Valid methods: {', '.join(sorted(VALID_PAYMENT_METHODS))}"
        )

    # 4. Find the invoice by invoice_number.
    invoice = session.execute(
        sa_select(Invoice).where(
            Invoice.invoice_number == invoice_number,
            Invoice.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if invoice is None:
        return f"Invoice '{invoice_number}' not found."

    # 5. Validate invoice is in a payable state.
    if invoice.state not in ("ISSUED", "PARTIALLY_PAID"):
        return (
            f"Invoice '{invoice_number}' is in state '{invoice.state}' "
            f"and cannot accept payments. Only ISSUED or PARTIALLY_PAID "
            f"invoices can be paid."
        )

    # 6. Validate invoice is linked to an order belonging to the representative.
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

    # 7. Validate amount does not exceed balance due.
    balance_due = decimal.Decimal(invoice.balance_due)
    if amount > balance_due:
        return (
            f"Payment amount {amount} exceeds balance due "
            f"{balance_due} for invoice '{invoice_number}'."
        )

    # 8. Build payload for deferred execution.
    payload = {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "customer_id": str(invoice.customer_id),
        "currency_id": str(invoice.currency_id),
        "amount": str(amount),
        "method": method,
        "reference": reference,
        "representative_id": str(rep.id),
        "requested_by": str(user.id),
    }
    return payload


def execute_record_payment(
    session: Session,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> str:
    """Execute the deferred payment recording after approval.

    Called by ``approval_execution_service.execute_approved_request()``
    when an approved ``bot_command:record-payment`` request is resolved.

    Uses the canonical ``payment_service.record_payment()`` — never
    duplicates payment recording logic.
    """
    from services import customer_ledger_service, payment_service

    invoice_id = uuid.UUID(payload["invoice_id"])
    customer_id = uuid.UUID(payload["customer_id"])
    currency_id = uuid.UUID(payload["currency_id"])
    amount = decimal.Decimal(payload["amount"])
    method = payload["method"]
    reference = payload.get("reference")

    payment = payment_service.record_payment(
        session,
        customer_id=customer_id,
        currency_id=currency_id,
        amount=amount,
        method=method,
        allocations=[(invoice_id, amount)],
        actor_user_id=actor_user_id,
        reference=reference,
        record_entry=customer_ledger_service.record_entry,
    )

    return (
        f"Payment {payment.payment_number} recorded successfully.\n"
        f"  Invoice: {payload['invoice_number']}\n"
        f"  Amount: {amount}\n"
        f"  Method: {method}"
    )
