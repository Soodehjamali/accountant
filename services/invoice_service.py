"""Service layer for the Invoice aggregate (``invoice`` T17 / ``invoice_line``
T18 / ``invoice_history`` H4 / ``invoice_order`` J1).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job. Mirrors the structure already established
by ``services/order_service.py`` / ``services/customer_service.py``.

State machine: implements the graph derived from ``09_Decisions.md``
ADR-006 (immutability at ISSUED, not at PAID/CLOSED_CORRECTED) combined
with ``07_DATABASE_SPEC.md`` §T17's ``InvoiceState`` vocabulary.

ADR-006 key decisions applied here:
* Immutability triggers at ISSUED (any state other than DRAFT or VOID):
  header fields (subtotal, tax_total, discount_total, grand_total,
  customer_id, currency_id) and all line items are frozen.
* ``amount_paid`` / ``balance_due`` remain writable post-ISSUED as a
  column-level exception (the reconciliation-service-role GRANT in
  deployment), but in this application service we allow
  ``record_payment`` to update them directly since we don't have the
  full payment-allocation ledger yet.
* VOID is reachable only from DRAFT (per the spec's "pre-ISSUED only"
  soft-delete strategy note and ADR-006's own reasoning about
  ISSUED-but-unpaid invoices being non-silently-editable).

Every state transition writes an ``invoice_history`` row -- see
``_transition`` below, the single choke point every state-changing
function funnels through.

Explicitly OUT OF SCOPE for this module:
* ``payment_allocation`` (J2) -- the full payment-to-invoice matching
  ledger is not yet built; ``record_payment`` here is a simplified
  direct-amount update against the invoice's own cache columns.
* ``credit_note`` (T20) -- corrections via credit note are a future
  milestone.
* The BEFORE UPDATE immutability trigger in the database -- the
  application layer enforces immutability via state checks; the DB
  trigger is a defense-in-depth concern applied at migration time.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.customer import Customer
from database.models.invoice import Invoice
from database.models.invoice_history import InvoiceHistory
from database.models.invoice_line import InvoiceLine
from database.models.invoice_order import InvoiceOrder
from database.models.order import Order
from database.models.order_line import OrderLine
from services import audit_service, order_service

#: Permission code gating all invoice mutations (create, issue, pay, void).
INVOICE_MANAGE_PERMISSION_CODE = "INVOICE_MANAGE"

#: The accepted Invoice state graph.  Keys are the "from" state; values are
#: the set of states directly reachable from it.  Derived from ADR-006 +
#: §T17's InvoiceState CHECK vocabulary.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"ISSUED", "VOID"}),
    "ISSUED": frozenset({"PARTIALLY_PAID", "PAID", "VOID", "CLOSED_CORRECTED"}),
    "PARTIALLY_PAID": frozenset({"PAID", "VOID", "CLOSED_CORRECTED"}),
    "PAID": frozenset({"CLOSED_CORRECTED"}),
    "CLOSED_CORRECTED": frozenset(),
    "VOID": frozenset(),
}

#: States in which the invoice is considered "immutable" per ADR-006.
#: All header fields + lines are frozen; only amount_paid/balance_due
#: may still be written (column-level exception).
_IMMUTABLE_STATES = frozenset(
    {"ISSUED", "PARTIALLY_PAID", "PAID", "CLOSED_CORRECTED"}
)


class InvoiceNotFoundError(LookupError):
    """Raised when a referenced ``invoice_id`` has no matching row."""

    def __init__(self, invoice_id: uuid.UUID) -> None:
        super().__init__(f"No invoice with id '{invoice_id}' exists.")
        self.invoice_id = invoice_id


class OrderNotFoundError(LookupError):
    """Raised when a referenced ``order_id`` has no matching row."""

    def __init__(self, order_id: uuid.UUID) -> None:
        super().__init__(f"No order with id '{order_id}' exists.")
        self.order_id = order_id


class OrderNotShippedError(ValueError):
    """Raised when attempting to invoice an order not in SHIPPED state."""

    def __init__(self, order_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Order '{order_id}' is in state '{state}'; only SHIPPED "
            f"orders can be invoiced."
        )
        self.order_id = order_id
        self.state = state


class InvalidInvoiceStateTransitionError(ValueError):
    """Raised when a transition isn't a valid edge in ``ALLOWED_TRANSITIONS``."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(
            f"Cannot transition an invoice from '{from_state}' to '{to_state}'."
        )
        self.from_state = from_state
        self.to_state = to_state


class InvoiceImmutableError(ValueError):
    """Raised when attempting to modify an immutable (post-ISSUED) invoice."""

    def __init__(self, invoice_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Invoice '{invoice_id}' is in immutable state '{state}'."
        )
        self.invoice_id = invoice_id
        self.state = state


class PaymentExceedsBalanceError(ValueError):
    """Raised when a payment amount exceeds the remaining balance_due."""

    def __init__(self, requested: decimal.Decimal, balance_due: decimal.Decimal) -> None:
        super().__init__(
            f"Payment amount {requested} exceeds balance due {balance_due}."
        )
        self.requested = requested
        self.balance_due = balance_due


class VoidOnlyFromDraftError(ValueError):
    """Raised when attempting to void an invoice that is not in DRAFT."""

    def __init__(self, state: str) -> None:
        super().__init__(
            f"Invoices can only be voided from DRAFT state; current state is '{state}'."
        )
        self.state = state


class OrderNotInShippableStateForInvoiceError(ValueError):
    """Raised when the related order is not in SHIPPED state when
    issue_invoice tries to coordinate the SHIPPED -> INVOICED transition.

    This means the order was already invoiced, cancelled, or is in some
    other state that doesn't allow mark_invoiced.  The invoice issuance
    is rolled back (the entire session is invalid) because the caller
    should not proceed with issuing an invoice for a non-SHIPPED order.
    """

    def __init__(self, order_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Order '{order_id}' is in state '{state}'; cannot transition to INVOICED."
        )
        self.order_id = order_id
        self.state = state


def _get_invoice_or_raise(session: Session, invoice_id: uuid.UUID) -> Invoice:
    invoice = session.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.deleted_at.is_(None))
    ).scalar_one_or_none()
    if invoice is None:
        raise InvoiceNotFoundError(invoice_id)
    return invoice


def _generate_invoice_number() -> str:
    """A simple, collision-safe business key: date-stamped + random suffix."""
    today = datetime.date.today().strftime("%Y%m%d")
    return f"INV-{today}-{uuid.uuid4().hex[:8].upper()}"


def _transition(
    session: Session,
    invoice: Invoice,
    to_state: str,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> Invoice:
    """The single choke point every state-changing function funnels through:
    validates the edge against ``ALLOWED_TRANSITIONS``, applies it, and
    writes the matching ``invoice_history`` row.

    Raises:
        InvalidInvoiceStateTransitionError: not a valid edge.
    """
    from_state = invoice.state
    if to_state not in ALLOWED_TRANSITIONS.get(from_state, frozenset()):
        raise InvalidInvoiceStateTransitionError(from_state, to_state)

    invoice.state = to_state
    invoice.updated_by = actor_user_id
    session.add(
        InvoiceHistory(
            invoice_id=invoice.id,
            actor_user_id=actor_user_id,
            from_state=from_state,
            to_state=to_state,
            note=note,
        )
    )
    session.flush()

    audit_service.record(
        session,
        entity_type="invoice",
        entity_id=invoice.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": from_state},
        after={"state": to_state, "note": note},
    )
    session.flush()
    return invoice


def create_invoice_from_order(
    session: Session,
    *,
    order_id: uuid.UUID,
    created_by: uuid.UUID,
    due_days: int | None = None,
    note: str | None = None,
) -> Invoice:
    """Create a new ``DRAFT`` invoice from a ``SHIPPED`` order.

    Copies order lines into invoice lines (unit_price frozen per BR-P3),
    computes totals, and links the invoice to the order via
    ``invoice_order`` (J1).  The invoice starts in DRAFT and must be
    explicitly issued via ``issue_invoice``.

    ``due_days`` sets ``due_at`` relative to issue time (default 30 days
    from now if not provided).

    Raises:
        OrderNotFoundError: no matching order.
        OrderNotShippedError: order is not in SHIPPED state.
    """
    order = session.execute(
        select(Order).where(Order.id == order_id, Order.deleted_at.is_(None))
    ).scalar_one_or_none()
    if order is None:
        raise OrderNotFoundError(order_id)
    if order.state != "SHIPPED":
        raise OrderNotShippedError(order_id, order.state)

    # Fetch order lines to copy into invoice lines.
    order_lines = session.execute(
        select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.created_at)
    ).scalars().all()

    invoice_number = _generate_invoice_number()
    now = datetime.datetime.now(datetime.timezone.utc)

    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=order.customer_id,
        currency_id=order.currency_id,
        state="DRAFT",
        subtotal=order.subtotal,
        tax_total=order.tax_total,
        discount_total=order.discount_total,
        grand_total=order.grand_total,
        amount_paid=decimal.Decimal("0"),
        balance_due=order.grand_total,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(invoice)
    session.flush()

    # Link invoice to order (J1 junction).
    session.add(InvoiceOrder(invoice_id=invoice.id, order_id=order.id))
    session.flush()

    # Copy order lines to invoice lines.
    for ol in order_lines:
        line_total = (ol.unit_price * ol.qty_ordered) - ol.discount_value
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                order_line_id=ol.id,
                product_id=ol.product_id,
                description=f"Order line {ol.product_id}",
                qty=ol.qty_ordered,
                unit_price=ol.unit_price,
                tax_rate=decimal.Decimal("0"),
                tax_amount=decimal.Decimal("0"),
                discount_value=ol.discount_value,
                line_total=line_total,
                created_by=created_by,
                updated_by=created_by,
            )
        )
    session.flush()

    # Write history (create is not itself a state transition, but we
    # record the initial DRAFT creation for traceability).
    session.add(
        InvoiceHistory(
            invoice_id=invoice.id,
            actor_user_id=created_by,
            from_state="DRAFT",
            to_state="DRAFT",
            note=note or "Invoice created from order",
        )
    )
    session.flush()

    audit_service.record(
        session,
        entity_type="invoice",
        entity_id=invoice.id,
        action="CREATE",
        actor_user_id=created_by,
        after={
            "invoice_number": invoice_number,
            "customer_id": str(order.customer_id),
            "state": "DRAFT",
            "grand_total": str(invoice.grand_total),
        },
    )
    session.flush()

    return invoice


def get_invoice(session: Session, invoice_id: uuid.UUID) -> Invoice:
    """Return a single, non-deleted invoice.  Raises: InvoiceNotFoundError."""
    return _get_invoice_or_raise(session, invoice_id)


def list_invoices(
    session: Session,
    *,
    customer_id: uuid.UUID | None = None,
    state: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[Invoice]:
    """List non-deleted invoices, optionally filtered."""
    query = select(Invoice).where(Invoice.deleted_at.is_(None))
    if customer_id is not None:
        query = query.where(Invoice.customer_id == customer_id)
    if state is not None:
        query = query.where(Invoice.state == state)
    query = query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def list_invoice_lines(session: Session, invoice_id: uuid.UUID) -> Iterable[InvoiceLine]:
    """Return all lines for an invoice.  Raises: InvoiceNotFoundError."""
    _get_invoice_or_raise(session, invoice_id)
    return session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id).order_by(InvoiceLine.created_at)
    ).scalars().all()


def get_invoice_history(session: Session, invoice_id: uuid.UUID) -> Iterable[InvoiceHistory]:
    """Return the state-change history for an invoice.  Raises: InvoiceNotFoundError."""
    _get_invoice_or_raise(session, invoice_id)
    return session.execute(
        select(InvoiceHistory)
        .where(InvoiceHistory.invoice_id == invoice_id)
        .order_by(InvoiceHistory.event_at)
    ).scalars().all()


def issue_invoice(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    due_days: int = 30,
    note: str | None = None,
    record_entry: Callable[..., Any] | None = None,
) -> Invoice:
    """``DRAFT -> ISSUED``.

    Sets ``issued_at`` and ``due_at`` (due_days from now).  After
    issuance the invoice is immutable per ADR-006 -- no header or
    line edits are permitted; only ``amount_paid``/``balance_due``
    may still be updated by ``record_payment``.

    After the successful DRAFT -> ISSUED transition, this function:

    1. Coordinates with the Order domain by calling
       ``order_service.mark_invoiced()`` on the related order (via the
       ``invoice_order`` J1 junction), transitioning the order from
       SHIPPED -> INVOICED.
    2. Records a customer ledger entry (T22) with type
       ``INVOICE_ISSUED`` via the ``record_entry`` callback, if one
       is provided.

    ``record_entry`` is an injectable dependency (see module docstring
    for the credit_note_service pattern).  When ``None`` (the default),
    no ledger entry is written -- the caller should supply the real
    ``customer_ledger_service.record_entry`` function to complete the
    integration.

    The callback signature should be::

        record_entry(session, *, customer_id, reference_type="invoice",
                     reference_id, signed_amount, currency_id,
                     entry_type="INVOICE_ISSUED", actor_user_id)

    Design rationale for rollback: both the invoice state change and
    the order state change happen within the same session.  If the
    order transition fails, allowing the invoice to remain ISSUED
    while the order is still SHIPPED (or already INVOICED) creates
    an inconsistent cross-aggregate state.  Rolling back the entire
    session ensures atomicity -- the caller can retry after resolving
    the order's state.

    Raises:
        InvoiceNotFoundError, InvalidInvoiceStateTransitionError,
        OrderNotInShippableStateForInvoiceError.
    """
    invoice = _get_invoice_or_raise(session, invoice_id)
    now = datetime.datetime.now(datetime.timezone.utc)
    invoice.issued_at = now
    invoice.due_at = now + datetime.timedelta(days=due_days)
    invoice.updated_by = actor_user_id
    session.flush()
    issued = _transition(session, invoice, "ISSUED", actor_user_id=actor_user_id, note=note)

    # --- Order coordination (ADR-005 milestone open question resolved) ---
    # Look up the related order via the invoice_order (J1) junction.
    invoice_order_link = session.execute(
        select(InvoiceOrder).where(InvoiceOrder.invoice_id == invoice.id)
    ).scalar_one_or_none()
    if invoice_order_link is not None:
        try:
            order_service.mark_invoiced(
                session,
                invoice_order_link.order_id,
                actor_user_id=actor_user_id,
                note=f"Order invoiced via invoice {invoice.invoice_number}",
            )
        except order_service.InvalidOrderStateTransitionError as exc:
            raise OrderNotInShippableStateForInvoiceError(
                invoice_order_link.order_id, exc.from_state
            ) from exc

    # --- Customer Ledger entry (T22) ---
    # INVOICE_ISSUED is a +debit (positive signed_amount) per the
    # spec's "+debit / -credit" convention.  The invoice's grand_total
    # increases the customer's AR balance.
    if record_entry is not None:
        record_entry(
            session,
            customer_id=invoice.customer_id,
            reference_type="invoice",
            reference_id=invoice.id,
            signed_amount=decimal.Decimal(invoice.grand_total),
            currency_id=invoice.currency_id,
            entry_type="INVOICE_ISSUED",
            actor_user_id=actor_user_id,
        )

    return issued


def record_payment(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    amount: decimal.Decimal,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> Invoice:
    """Record a payment against an invoice.

    Updates ``amount_paid`` and ``balance_due`` (the column-level
    exception to ADR-006's immutability).  Transitions state:
    * ISSUED + partial -> PARTIALLY_PAID
    * ISSUED + full -> PAID
    * PARTIALLY_PAID + full -> PAID

    ADR-006 note: ``amount_paid``/``balance_due`` are the non-authoritative
    cache columns that remain writable post-ISSUED.  In a full deployment
    these would be updated only by the reconciliation job via column-level
    GRANT; here we update them directly as a simplification pending the
    ``payment_allocation`` (J2) ledger.

    Raises:
        InvoiceNotFoundError, PaymentExceedsBalanceError.
    """
    invoice = _get_invoice_or_raise(session, invoice_id)
    if invoice.state not in ("ISSUED", "PARTIALLY_PAID"):
        raise InvalidInvoiceStateTransitionError(invoice.state, "PARTIALLY_PAID")

    new_amount_paid = decimal.Decimal(invoice.amount_paid) + amount
    new_balance = decimal.Decimal(invoice.grand_total) - new_amount_paid

    if new_balance < 0:
        raise PaymentExceedsBalanceError(amount, invoice.balance_due)

    invoice.amount_paid = new_amount_paid
    invoice.balance_due = new_balance
    invoice.updated_by = actor_user_id
    session.flush()

    # Determine target state.
    if new_balance <= 0:
        target = "PAID"
        invoice.balance_due = decimal.Decimal("0")
        invoice.amount_paid = invoice.grand_total
        invoice.closed_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        target = "PARTIALLY_PAID"

    return _transition(session, invoice, target, actor_user_id=actor_user_id, note=note)


def void_invoice(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> Invoice:
    """Void an invoice.

    Per ADR-006 and §T17's Soft Delete Strategy ("Supported pre-ISSUED
    only"), voiding is only permitted from DRAFT.  Post-ISSUED invoices
    are never voided -- they are corrected only via ``credit_note`` (T20),
    which is out of scope for this milestone.

    Raises:
        InvoiceNotFoundError, VoidOnlyFromDraftError.
    """
    invoice = _get_invoice_or_raise(session, invoice_id)
    if invoice.state != "DRAFT":
        raise VoidOnlyFromDraftError(invoice.state)
    return _transition(session, invoice, "VOID", actor_user_id=actor_user_id, note=note)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "INVOICE_MANAGE_PERMISSION_CODE",
    "InvoiceImmutableError",
    "InvoiceNotFoundError",
    "InvalidInvoiceStateTransitionError",
    "OrderNotFoundError",
    "OrderNotInShippableStateForInvoiceError",
    "OrderNotShippedError",
    "PaymentExceedsBalanceError",
    "VoidOnlyFromDraftError",
    "create_invoice_from_order",
    "get_invoice",
    "get_invoice_history",
    "issue_invoice",
    "list_invoice_lines",
    "list_invoices",
    "record_payment",
    "void_invoice",
]
