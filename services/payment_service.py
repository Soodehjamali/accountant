"""Service layer for the Payment aggregate (``payment`` T19 /
``payment_allocation`` J2).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.  Mirrors the structure already established
by ``services/invoice_service.py`` / ``services/order_service.py``.

Payment is an append-only ledger (AAC per ``database/models/payment.py``):
a mis-posted payment is corrected via a compensating reversal, never by
mutating the original row.  ``payment_allocation`` (J2) resolves the N:N
between payments and invoices, enabling split/cross-invoice allocations.

Business constraints (application-enforced, no DB trigger):
* ``SUM(allocated_amount) per payment_id <= payment.amount``
* ``SUM(allocated_amount) per invoice_id <= invoice.grand_total``
* Each ``allocated_amount > 0``
* Invoice must be in ISSUED or PARTIALLY_PAID state to receive allocations
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.invoice import Invoice
from database.models.payment import Payment
from database.models.payment_allocation import PaymentAllocation
from services import audit_service

#: Permission code gating all payment mutations.
PAYMENT_MANAGE_PERMISSION_CODE = "PAYMENT_MANAGE"


class PaymentExceedsTotalAllocationsError(ValueError):
    """Raised when the sum of allocation amounts exceeds payment.amount."""

    def __init__(self, total_allocated: decimal.Decimal, payment_amount: decimal.Decimal) -> None:
        super().__init__(
            f"Total allocations ({total_allocated}) exceed payment amount ({payment_amount})."
        )
        self.total_allocated = total_allocated
        self.payment_amount = payment_amount


class InvoiceAllocationExceedsBalanceError(ValueError):
    """Raised when an allocation to an invoice exceeds its balance_due."""

    def __init__(self, invoice_id: uuid.UUID, amount: decimal.Decimal, balance_due: decimal.Decimal) -> None:
        super().__init__(
            f"Allocation ({amount}) for invoice '{invoice_id}' exceeds balance_due ({balance_due})."
        )
        self.invoice_id = invoice_id
        self.amount = amount
        self.balance_due = balance_due


class InvoiceNotPayableError(ValueError):
    """Raised when an invoice is not in a state that accepts payments."""

    def __init__(self, invoice_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Invoice '{invoice_id}' is in state '{state}' and cannot accept payments."
        )
        self.invoice_id = invoice_id
        self.state = state


class PaymentNotFoundError(LookupError):
    """Raised when a referenced payment_id has no matching row."""

    def __init__(self, payment_id: uuid.UUID) -> None:
        super().__init__(f"No payment with id '{payment_id}' exists.")
        self.payment_id = payment_id


def _generate_payment_number() -> str:
    """A simple, collision-safe business key: date-stamped + random suffix."""
    today = datetime.date.today().strftime("%Y%m%d")
    return f"PAY-{today}-{uuid.uuid4().hex[:8].upper()}"


def _get_invoice_or_raise(session: Session, invoice_id: uuid.UUID) -> Invoice:
    invoice = session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    ).scalar_one_or_none()
    if invoice is None:
        from services.invoice_service import InvoiceNotFoundError as Exc
        raise Exc(invoice_id)
    return invoice


def record_payment(
    session: Session,
    *,
    customer_id: uuid.UUID,
    currency_id: uuid.UUID,
    amount: decimal.Decimal,
    method: str,
    allocations: list[tuple[uuid.UUID, decimal.Decimal]],
    actor_user_id: uuid.UUID,
    reference: str | None = None,
    received_at: datetime.datetime | None = None,
) -> Payment:
    """Record a payment and allocate it to one or more invoices.

    Creates a ``Payment`` row (append-only) plus one or more
    ``PaymentAllocation`` rows.  Updates each invoice's
    ``amount_paid`` / ``balance_due`` and transitions state as needed.

    Args:
        allocations: List of ``(invoice_id, allocated_amount)`` tuples.

    Business constraints enforced here (no DB trigger exists):
    * ``SUM(allocated_amount) <= payment.amount``
    * Each ``allocated_amount <= invoice.balance_due``
    * Each invoice must be in ISSUED or PARTIALLY_PAID state

    Raises:
        PaymentExceedsTotalAllocationsError: allocations exceed payment amount.
        InvoiceAllocationExceedsBalanceError: allocation exceeds invoice balance.
        InvoiceNotPayableError: invoice is not in a payable state.
    """
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")

    if not allocations:
        raise ValueError("At least one allocation is required.")

    total_allocated = sum(a[1] for a in allocations)
    if total_allocated > amount:
        raise PaymentExceedsTotalAllocationsError(total_allocated, amount)

    # Validate allocations before writing anything.
    invoices: list[tuple[Invoice, decimal.Decimal]] = []
    for invoice_id, alloc_amount in allocations:
        if alloc_amount <= 0:
            raise ValueError(f"Allocation amount for invoice '{invoice_id}' must be positive.")
        invoice = _get_invoice_or_raise(session, invoice_id)
        if invoice.state not in ("ISSUED", "PARTIALLY_PAID"):
            raise InvoiceNotPayableError(invoice_id, invoice.state)
        if alloc_amount > invoice.balance_due:
            raise InvoiceAllocationExceedsBalanceError(invoice_id, alloc_amount, invoice.balance_due)
        invoices.append((invoice, alloc_amount))

    now = received_at or datetime.datetime.now(datetime.timezone.utc)

    payment = Payment(
        payment_number=_generate_payment_number(),
        customer_id=customer_id,
        currency_id=currency_id,
        received_by=actor_user_id,
        amount=amount,
        method=method,
        reference=reference,
        received_at=now,
        unallocated_amount=amount - total_allocated,
        created_by=actor_user_id,
    )
    session.add(payment)
    session.flush()

    for invoice, alloc_amount in invoices:
        allocation = PaymentAllocation(
            payment_id=payment.id,
            invoice_id=invoice.id,
            allocated_amount=alloc_amount,
            allocated_by=actor_user_id,
        )
        session.add(allocation)

        # Update invoice amount_paid / balance_due (non-authoritative cache).
        new_amount_paid = decimal.Decimal(invoice.amount_paid) + alloc_amount
        new_balance = decimal.Decimal(invoice.grand_total) - new_amount_paid

        invoice.amount_paid = new_amount_paid
        invoice.balance_due = max(new_balance, decimal.Decimal("0"))
        invoice.updated_by = actor_user_id

        # Transition invoice state if needed.
        if invoice.balance_due <= 0 and invoice.state in ("ISSUED", "PARTIALLY_PAID"):
            invoice.balance_due = decimal.Decimal("0")
            invoice.amount_paid = invoice.grand_total
            invoice.closed_at = datetime.datetime.now(datetime.timezone.utc)
            target_state = "PAID"
        elif invoice.state == "ISSUED":
            target_state = "PARTIALLY_PAID"
        else:
            target_state = invoice.state

        if target_state != invoice.state:
            from_state = invoice.state
            invoice.state = target_state
            invoice.updated_by = actor_user_id
            # Write invoice_history row.
            from database.models.invoice_history import InvoiceHistory
            session.add(
                InvoiceHistory(
                    invoice_id=invoice.id,
                    actor_user_id=actor_user_id,
                    from_state=from_state,
                    to_state=target_state,
                )
            )

    session.flush()

    audit_service.record(
        session,
        entity_type="payment",
        entity_id=payment.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        after={
            "payment_number": payment.payment_number,
            "customer_id": str(customer_id),
            "amount": str(amount),
            "method": method,
            "allocations_count": len(allocations),
        },
    )
    session.flush()

    return payment


def get_payment(session: Session, payment_id: uuid.UUID) -> Payment:
    """Return a single payment. Raises: PaymentNotFoundError."""
    payment = session.execute(
        select(Payment).where(Payment.id == payment_id)
    ).scalar_one_or_none()
    if payment is None:
        raise PaymentNotFoundError(payment_id)
    return payment


def list_payment_allocations(session: Session, invoice_id: uuid.UUID) -> Iterable[PaymentAllocation]:
    """Return all payment allocations for a given invoice."""
    return session.execute(
        select(PaymentAllocation)
        .where(PaymentAllocation.invoice_id == invoice_id)
        .order_by(PaymentAllocation.allocated_at)
    ).scalars().all()


def list_payments_for_invoice(session: Session, invoice_id: uuid.UUID) -> Iterable[Payment]:
    """Return all payments that have allocations to a given invoice."""
    allocation_subq = (
        select(PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == invoice_id)
        .distinct()
        .subquery()
    )
    return session.execute(
        select(Payment)
        .where(Payment.id.in_(select(allocation_subq.c.payment_id)))
        .order_by(Payment.received_at.desc())
    ).scalars().all()


__all__ = [
    "PAYMENT_MANAGE_PERMISSION_CODE",
    "InvoiceAllocationExceedsBalanceError",
    "InvoiceNotPayableError",
    "PaymentExceedsTotalAllocationsError",
    "PaymentNotFoundError",
    "get_payment",
    "list_payment_allocations",
    "list_payments_for_invoice",
    "record_payment",
]
