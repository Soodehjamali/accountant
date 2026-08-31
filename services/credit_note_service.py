"""Service layer for the Credit Note aggregate (``credit_note`` T20 /
``credit_note_line`` T21).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.  Mirrors the structure already established
by ``services/invoice_service.py`` / ``services/order_service.py``.

State machine: DRAFT -> ISSUED -> APPLIED; DRAFT -> VOID.
Derived from ``07_DATABASE_SPEC.md`` §T20's ``CreditNoteState`` vocabulary
and the business constraints stated there.

Key design decisions (per spec §T20 point 7):
* Applying a credit note never edits the original invoice's rows directly.
* Applying reduces the customer's balance via a ``customer_ledger_entry``
  (T22) once APPLIED.
* Applying marks the original invoice ``CLOSED_CORRECTED`` (cross-table,
  same session for atomicity -- mirrors the
  ``invoice_service.issue_invoice()`` -> ``order_service.mark_invoiced()``
  pattern already used for cross-aggregate transitions).

DEPENDENCY INJECTION -- Customer Ledger:
``apply_credit_note`` requires a ``record_entry`` callback to write the
customer_ledger_entry (T22).  This callback is injected as a parameter
with a default of ``None``; when ``None``, the function raises
``NotImplementedError`` with a clear message pointing at the pending
Customer Ledger milestone.  In production code, the real ledger service's
entry-writing function will be supplied as the default wiring (e.g. in
``main.py``'s dependency setup).  Tests inject a fake/mock callable to
verify the full apply path end-to-end without needing the real service.

Every state transition writes audit_log entries -- see ``_transition``
below, the single choke point every state-changing function funnels
through.

Explicitly OUT OF SCOPE for this module:
* The ``customer_ledger_entry`` (T22) service itself -- that is a separate
  milestone; we only define the callback interface here.
* Inventory reversal (BR-F4) -- credit notes for returns would trigger
  reverse inventory transactions, but that integration is future scope.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.credit_note import CreditNote
from database.models.credit_note_line import CreditNoteLine
from database.models.invoice import Invoice
from services import audit_service

#: Permission code gating all credit note mutations.
CREDIT_NOTE_MANAGE_PERMISSION_CODE = "CREDIT_NOTE_MANAGE"

#: The accepted Credit Note state graph.  Keys are the "from" state;
#: values are the set of states directly reachable from it.
#: Derived from §T20's CreditNoteState CHECK vocabulary.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"ISSUED", "VOID"}),
    "ISSUED": frozenset({"APPLIED"}),
    "APPLIED": frozenset(),
    "VOID": frozenset(),
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CreditNoteNotFoundError(LookupError):
    """Raised when a referenced ``credit_note_id`` has no matching row."""

    def __init__(self, credit_note_id: uuid.UUID) -> None:
        super().__init__(f"No credit note with id '{credit_note_id}' exists.")
        self.credit_note_id = credit_note_id


class InvalidCreditNoteStateTransitionError(ValueError):
    """Raised when a transition isn't a valid edge in ``ALLOWED_TRANSITIONS``."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(
            f"Cannot transition a credit note from '{from_state}' to '{to_state}'."
        )
        self.from_state = from_state
        self.to_state = to_state


class CreditNoteImmutableError(ValueError):
    """Raised when attempting to modify an immutable (post-ISSUED) credit note."""

    def __init__(self, credit_note_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Credit note '{credit_note_id}' is in immutable state '{state}'."
        )
        self.credit_note_id = credit_note_id
        self.state = state


class InvoiceNotCreditableError(ValueError):
    """Raised when an invoice cannot be corrected via credit note."""

    def __init__(self, invoice_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Invoice '{invoice_id}' is in state '{state}' and cannot be "
            f"corrected via credit note."
        )
        self.invoice_id = invoice_id
        self.state = state


class CreditNoteLineQtyNonPositiveError(ValueError):
    """Raised when a credit note line has qty <= 0."""

    def __init__(self, description: str, qty: decimal.Decimal) -> None:
        super().__init__(
            f"Credit note line '{description}' has non-positive qty ({qty})."
        )
        self.description = description
        self.qty = qty


class CreditNoteAmountNonPositiveError(ValueError):
    """Raised when the computed total_amount is <= 0."""

    def __init__(self, total: decimal.Decimal) -> None:
        super().__init__(
            f"Credit note total_amount must be positive, got {total}."
        )
        self.total = total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_credit_note_number() -> str:
    """Collision-safe business key: date-stamped + random suffix."""
    today = datetime.date.today().strftime("%Y%m%d")
    return f"CN-{today}-{uuid.uuid4().hex[:8].upper()}"


def _get_credit_note_or_raise(session: Session, credit_note_id: uuid.UUID) -> CreditNote:
    credit_note = session.execute(
        select(CreditNote).where(CreditNote.id == credit_note_id)
    ).scalar_one_or_none()
    if credit_note is None:
        raise CreditNoteNotFoundError(credit_note_id)
    return credit_note


def _transition(
    session: Session,
    credit_note: CreditNote,
    to_state: str,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CreditNote:
    """Single choke-point for every credit note state transition.

    Raises:
        InvalidCreditNoteStateTransitionError: not a valid edge.
    """
    from_state = credit_note.state
    if to_state not in ALLOWED_TRANSITIONS.get(from_state, frozenset()):
        raise InvalidCreditNoteStateTransitionError(from_state, to_state)

    credit_note.state = to_state
    credit_note.updated_by = actor_user_id
    session.flush()

    audit_service.record(
        session,
        entity_type="credit_note",
        entity_id=credit_note.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": from_state},
        after={"state": to_state, "note": note},
    )
    session.flush()
    return credit_note


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_credit_note(
    session: Session,
    *,
    invoice_id: uuid.UUID,
    reason_code_id: uuid.UUID,
    lines: list[dict[str, Any]],
    created_by: uuid.UUID,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    note: str | None = None,
) -> CreditNote:
    """Create a new ``DRAFT`` credit note against an existing invoice.

    ``lines`` is a list of dicts with keys:
    ``invoice_line_id`` (optional UUID), ``description`` (str),
    ``qty`` (Decimal, must be > 0), ``unit_price`` (Decimal, must be > 0).

    The ``total_amount`` is computed as ``sum(qty * unit_price)`` across
    all lines.

    ``note`` has no dedicated column on ``credit_note`` (unlike ``invoice``,
    this table has no ``credit_note_history`` table to hold it) -- it is
    recorded in the ``audit_log`` CREATE entry's ``after`` payload instead,
    matching how every transition function on this module already threads
    ``note`` through ``audit_service.record``'s ``after`` payload.

    Raises:
        InvoiceNotCreditableError: invoice is in a non-creditable state.
        CreditNoteLineQtyNonPositiveError: a line has qty <= 0.
        CreditNoteAmountNonPositiveError: computed total <= 0.
    """
    # Validate invoice exists and is in a creditable state.
    invoice = session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    ).scalar_one_or_none()
    if invoice is None:
        from services.invoice_service import InvoiceNotFoundError as Exc
        raise Exc(invoice_id)
    if invoice.state in ("DRAFT", "VOID", "CLOSED_CORRECTED"):
        raise InvoiceNotCreditableError(invoice_id, invoice.state)

    # Validate lines and compute total.
    total_amount = decimal.Decimal("0")
    for line in lines:
        qty = decimal.Decimal(str(line["qty"]))
        if qty <= 0:
            raise CreditNoteLineQtyNonPositiveError(line["description"], qty)
        line_total = qty * decimal.Decimal(str(line["unit_price"]))
        total_amount += line_total

    if total_amount <= 0:
        raise CreditNoteAmountNonPositiveError(total_amount)

    now = datetime.datetime.now(datetime.timezone.utc)
    credit_note_number = _generate_credit_note_number()

    credit_note = CreditNote(
        credit_note_number=credit_note_number,
        invoice_id=invoice_id,
        customer_id=invoice.customer_id,
        issued_by=created_by,
        reason_code_id=reason_code_id,
        reference_type=reference_type,
        reference_id=reference_id,
        total_amount=total_amount,
        state="DRAFT",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(credit_note)
    session.flush()

    # Create line items.
    for line in lines:
        qty = decimal.Decimal(str(line["qty"]))
        unit_price = decimal.Decimal(str(line["unit_price"]))
        line_total = qty * unit_price
        session.add(
            CreditNoteLine(
                credit_note_id=credit_note.id,
                invoice_line_id=line.get("invoice_line_id"),
                description=line["description"],
                qty=qty,
                unit_price=unit_price,
                line_total=line_total,
                created_by=created_by,
                updated_by=created_by,
            )
        )
    session.flush()

    audit_service.record(
        session,
        entity_type="credit_note",
        entity_id=credit_note.id,
        action="CREATE",
        actor_user_id=created_by,
        after={
            "credit_note_number": credit_note_number,
            "invoice_id": str(invoice_id),
            "state": "DRAFT",
            "total_amount": str(total_amount),
            "note": note,
        },
    )
    session.flush()

    return credit_note


def get_credit_note(session: Session, credit_note_id: uuid.UUID) -> CreditNote:
    """Return a single credit note.  Raises: CreditNoteNotFoundError."""
    return _get_credit_note_or_raise(session, credit_note_id)


def list_credit_notes(
    session: Session,
    *,
    invoice_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[CreditNote]:
    """List credit notes, optionally filtered.

    ``invoice_id``: when set, returns only credit notes linked to the
    specified invoice.  ``customer_id``: when set, returns only credit
    notes for the specified customer.
    """
    query = select(CreditNote)
    if invoice_id is not None:
        query = query.where(CreditNote.invoice_id == invoice_id)
    if customer_id is not None:
        query = query.where(CreditNote.customer_id == customer_id)
    query = query.order_by(CreditNote.created_at.desc()).offset(skip).limit(limit)
    return list(session.execute(query).scalars().all())


def list_credit_note_lines(session: Session, credit_note_id: uuid.UUID) -> list[CreditNoteLine]:
    """Return all lines for a credit note.  Raises: CreditNoteNotFoundError."""
    _get_credit_note_or_raise(session, credit_note_id)
    return list(
        session.execute(
            select(CreditNoteLine)
            .where(CreditNoteLine.credit_note_id == credit_note_id)
            .order_by(CreditNoteLine.created_at)
        ).scalars().all()
    )


def issue_credit_note(
    session: Session,
    credit_note_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CreditNote:
    """``DRAFT -> ISSUED``.

    Sets ``issued_at``.  After issuance the credit note is no longer
    mutable -- only apply or void are reachable from ISSUED.

    Raises:
        CreditNoteNotFoundError, InvalidCreditNoteStateTransitionError.
    """
    credit_note = _get_credit_note_or_raise(session, credit_note_id)
    credit_note.issued_at = datetime.datetime.now(datetime.timezone.utc)
    credit_note.updated_by = actor_user_id
    session.flush()
    return _transition(session, credit_note, "ISSUED", actor_user_id=actor_user_id, note=note)


def apply_credit_note(
    session: Session,
    credit_note_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    record_entry: Callable[..., Any] | None = None,
    note: str | None = None,
) -> CreditNote:
    """``ISSUED -> APPLIED``.

    Per §T20 point 7:
    * Never edits the original invoice's rows directly.
    * Reduces the customer's balance via a ``customer_ledger_entry`` (T22)
      -- delegated to the ``record_entry`` callback.
    * Marks the original invoice ``CLOSED_CORRECTED`` (cross-table,
      same session for atomicity).

    ``record_entry`` is an injectable dependency (see module docstring).
    When ``None`` (the default), raises ``NotImplementedError`` -- the
    Customer Ledger milestone is responsible for supplying the real
    implementation.

    The callback signature should be::

        record_entry(session, *, customer_id, reference_type="credit_note",
                     reference_id, signed_amount, currency_id,
                     entry_type="CREDIT_NOTE_APPLIED", actor_user_id)

    Raises:
        CreditNoteNotFoundError, InvalidCreditNoteStateTransitionError,
        NotImplementedError (when record_entry is None).
    """
    if record_entry is None:
        raise NotImplementedError(
            "Customer Ledger service (T22) is not yet built. "
            "Pass a record_entry callback to apply_credit_note(). "
            "See credit_note_service.py module docstring for the "
            "expected callback signature."
        )

    credit_note = _get_credit_note_or_raise(session, credit_note_id)

    # Fetch the original invoice for cross-aggregate transition.
    invoice = session.execute(
        select(Invoice).where(Invoice.id == credit_note.invoice_id)
    ).scalar_one_or_none()
    if invoice is None:
        from services.invoice_service import InvoiceNotFoundError as Exc
        raise Exc(credit_note.invoice_id)

    # Transition credit note: ISSUED -> APPLIED.
    _transition(session, credit_note, "APPLIED", actor_user_id=actor_user_id, note=note)

    # Cross-aggregate: mark original invoice CLOSED_CORRECTED.
    # Same session, same atomicity pattern as
    # invoice_service.issue_invoice() -> order_service.mark_invoiced().
    # We set the state directly and write the history/audit ourselves,
    # mirroring the _transition choke-point pattern but without calling
    # invoice_service's private _transition function.
    from database.models.invoice_history import InvoiceHistory
    from services import invoice_service

    inv_from_state = invoice.state
    if "CLOSED_CORRECTED" not in invoice_service.ALLOWED_TRANSITIONS.get(
        inv_from_state, frozenset()
    ):
        raise invoice_service.InvalidInvoiceStateTransitionError(
            inv_from_state, "CLOSED_CORRECTED"
        )

    invoice.state = "CLOSED_CORRECTED"
    invoice.updated_by = actor_user_id
    session.add(
        InvoiceHistory(
            invoice_id=invoice.id,
            actor_user_id=actor_user_id,
            from_state=inv_from_state,
            to_state="CLOSED_CORRECTED",
            note=f"Invoice corrected via credit note {credit_note.credit_note_number}",
        )
    )
    session.flush()

    audit_service.record(
        session,
        entity_type="invoice",
        entity_id=invoice.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": inv_from_state},
        after={"state": "CLOSED_CORRECTED"},
    )
    session.flush()

    # Customer ledger entry -- the injectable callback handles the actual
    # write.  The signed_amount is NEGATIVE (credit) per the spec's
    # "+debit / -credit" convention for customer_ledger_entry.
    from services.bootstrap_service import ensure_default_currency
    default_currency = ensure_default_currency(session, actor_id=actor_user_id)

    record_entry(
        session,
        customer_id=credit_note.customer_id,
        reference_type="credit_note",
        reference_id=credit_note.id,
        signed_amount=-credit_note.total_amount,
        currency_id=default_currency.id,
        entry_type="CREDIT_NOTE_APPLIED",
        actor_user_id=actor_user_id,
    )

    return credit_note


def void_credit_note(
    session: Session,
    credit_note_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CreditNote:
    """``DRAFT -> VOID``.

    Pre-ISSUED soft-delete strategy, same convention as
    ``invoice_service.void_invoice()``.

    Raises:
        CreditNoteNotFoundError, InvalidCreditNoteStateTransitionError.
    """
    credit_note = _get_credit_note_or_raise(session, credit_note_id)
    return _transition(session, credit_note, "VOID", actor_user_id=actor_user_id, note=note)


__all__ = [
    "CREDIT_NOTE_MANAGE_PERMISSION_CODE",
    "ALLOWED_TRANSITIONS",
    "CreditNoteAmountNonPositiveError",
    "CreditNoteImmutableError",
    "CreditNoteLineQtyNonPositiveError",
    "CreditNoteNotFoundError",
    "InvoiceNotCreditableError",
    "InvalidCreditNoteStateTransitionError",
    "apply_credit_note",
    "create_credit_note",
    "get_credit_note",
    "issue_credit_note",
    "list_credit_note_lines",
    "void_credit_note",
]
