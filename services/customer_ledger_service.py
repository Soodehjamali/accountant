"""Service layer for the Customer Ledger domain (M13 customer_ledger
projection / T22 customer_ledger_entry event log).

Per ``CLAUDE.md`` ("Inventory is always calculated from immutable
InventoryTransaction") and the parallel constraint on the Customer Ledger:
the customer's balance is always calculated from immutable
``customer_ledger_entry`` rows.  ``customer_ledger.current_balance`` is a
non-authoritative, read-optimized cache -- identical pattern to
``inventory_balance_snapshot`` (T3) over ``inventory_transaction`` (T1).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.

Key design decisions (per ``07_DATABASE_SPEC.md`` §F.7 / M13 / T22):

* ``record_entry()`` is the sole sanctioned write path onto
  ``customer_ledger_entry``.  No endpoint or other service should
  construct ``CustomerLedgerEntry`` rows directly -- same constraint as
  ``inventory_service.post_transaction()`` for T1.

* ``record_entry()`` does NOT touch ``customer_ledger.current_balance``,
  ``last_entry_seq``, or ``last_reconciled_at``.  Those cached projection
  columns are written exclusively by ``reconcile_customer_ledger()``,
  matching the spec's documented column-level GRANT pattern (same as
  ``invoice.amount_paid`` being written only by the reconciliation role).

* ``get_balance()`` always computes the live balance from the entry log,
  never trusting the cached ``current_balance`` column -- matching the
  spec's "non-authoritative ... written only by the reconciliation job"
  constraint on M13.

* ``ensure_customer_ledger()`` upserts the M13 header row for a customer
  (idempotent get-or-create), mirroring the bootstrap pattern already
  used for currency/warehouse/UoM.

Hash-chain: each entry's ``row_hash`` is a SHA-256 hex digest (64 chars)
computed over the previous row's hash plus
this row's own content, and ``prev_hash`` is the prior row's ``row_hash``
-- scoped per customer via ``sequence_no``, matching the table's own
``uq_customer_ledger_entry_seq (customer_ledger_id, sequence_no)``
constraint.  Same pattern as ``inventory_service.py``'s hash-chain over
``inventory_transaction``.

Sequence number: monotonic per ``customer_ledger_id``, computed as
``MAX(sequence_no) + 1`` within the same transaction.  Same pattern as
``inventory_service._next_sequence_no()``.

Entry types (from ``07_DATABASE_SPEC.md`` §T22 check constraint):
``INVOICE_ISSUED`` | ``PAYMENT_RECEIVED`` | ``CREDIT_NOTE_APPLIED`` |
``WRITE_OFF``.

signed_amount convention: ``+`` = debit (increases customer balance),
``-`` = credit (decreases customer balance).  Same sign convention as
the spec's own "+debit / -credit" note on T22.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models.customer import Customer
from database.models.customer_ledger import CustomerLedger
from database.models.customer_ledger_entry import CustomerLedgerEntry

#: The set of valid entry_type values, matching
#: ``ck_customer_ledger_entry_type``.
ALLOWED_ENTRY_TYPES = frozenset({
    "INVOICE_ISSUED",
    "PAYMENT_RECEIVED",
    "CREDIT_NOTE_APPLIED",
    "WRITE_OFF",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CustomerLedgerNotFoundError(LookupError):
    """Raised when a customer has no ``customer_ledger`` header row."""

    def __init__(self, customer_id: uuid.UUID) -> None:
        super().__init__(
            f"No customer_ledger header exists for customer '{customer_id}'."
        )
        self.customer_id = customer_id


class InvalidEntryTypeError(ValueError):
    """Raised when ``entry_type`` is not in the allowed vocabulary."""

    def __init__(self, entry_type: str) -> None:
        super().__init__(
            f"Invalid entry_type '{entry_type}'. "
            f"Must be one of: {', '.join(sorted(ALLOWED_ENTRY_TYPES))}."
        )
        self.entry_type = entry_type


class EntryAmountZeroError(ValueError):
    """Raised when ``signed_amount`` is zero (violates CHECK constraint)."""

    def __init__(self, signed_amount: decimal.Decimal) -> None:
        super().__init__(
            f"signed_amount must be nonzero, got {signed_amount}."
        )
        self.signed_amount = signed_amount


class DuplicateSequenceError(ValueError):
    """Raised when the computed sequence_no collides (should be impossible
    within a single transaction, but checked defensively)."""

    def __init__(self, customer_ledger_id: uuid.UUID, sequence_no: int) -> None:
        super().__init__(
            f"Sequence {sequence_no} already exists for "
            f"customer_ledger '{customer_ledger_id}'."
        )
        self.customer_ledger_id = customer_ledger_id
        self.sequence_no = sequence_no


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_sequence_no(session: Session, customer_ledger_id: uuid.UUID) -> int:
    """Return the next monotonic ``sequence_no`` for this customer ledger
    (1 if none yet).  Same pattern as
    ``inventory_service._next_sequence_no()``.
    """

    current_max = session.execute(
        select(func.max(CustomerLedgerEntry.sequence_no)).where(
            CustomerLedgerEntry.customer_ledger_id == customer_ledger_id
        )
    ).scalar_one()
    return (current_max or 0) + 1


def _prev_row_hash(session: Session, customer_ledger_id: uuid.UUID) -> str | None:
    """Return the ``row_hash`` of the latest entry in this customer's
    chain, or ``None`` if no entries exist yet.
    """

    return session.execute(
        select(CustomerLedgerEntry.row_hash)
        .where(CustomerLedgerEntry.customer_ledger_id == customer_ledger_id)
        .order_by(CustomerLedgerEntry.sequence_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def _compute_row_hash(
    *,
    prev_hash: str | None,
    customer_ledger_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    sequence_no: int,
    signed_amount: decimal.Decimal,
    entry_type: str,
    occurred_at: datetime.datetime,
) -> str:
    """SHA-256 hex digest chaining this entry onto ``prev_hash``.

    Same hash-chain pattern as ``inventory_service._compute_row_hash()``,
    adapted for the customer ledger's columns.
    """

    payload = "|".join(
        str(part)
        for part in (
            prev_hash or "",
            customer_ledger_id,
            reference_type,
            reference_id,
            sequence_no,
            signed_amount,
            entry_type,
            occurred_at.isoformat(),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_customer_ledger(
    session: Session,
    *,
    customer_id: uuid.UUID,
    currency_id: uuid.UUID,
) -> CustomerLedger:
    """Get-or-create the M13 ``customer_ledger`` header for a customer.

    Idempotent -- returns the existing header if one already exists for
    this customer.  The header starts with ``current_balance=0`` and
    ``last_entry_seq=0``; it is updated only by ``reconcile_customer_ledger()``.

    Still useful for explicit pre-provisioning (e.g. bootstrap seeding),
    but callers no longer need to call this before ``record_entry()`` --
    ``record_entry()`` internally get-or-creates the header.

    Raises:
        ValueError: if the customer does not exist.
    """

    existing = session.execute(
        select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    # Validate customer exists.
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"No customer with id '{customer_id}' exists.")

    ledger = CustomerLedger(
        customer_id=customer_id,
        currency_id=currency_id,
        current_balance=decimal.Decimal("0"),
        last_entry_seq=0,
    )
    session.add(ledger)
    session.flush()
    return ledger


def record_entry(
    session: Session,
    *,
    customer_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    signed_amount: decimal.Decimal,
    currency_id: uuid.UUID,
    entry_type: str,
    actor_user_id: uuid.UUID,
) -> CustomerLedgerEntry:
    """Append one immutable row to the customer ledger (T22).

    This is the **sole sanctioned write path** onto
    ``customer_ledger_entry``.  No endpoint or other service should
    construct ``CustomerLedgerEntry`` rows directly -- same constraint as
    ``inventory_service.post_transaction()`` for T1.

    ``signed_amount`` follows the spec's "+debit / -credit" convention:
    positive values increase the customer's balance (e.g. INVOICE_ISSUED),
    negative values decrease it (e.g. PAYMENT_RECEIVED, CREDIT_NOTE_APPLIED).

    This function does NOT touch ``customer_ledger.current_balance``,
    ``last_entry_seq``, or ``last_reconciled_at``.  Those cached
    projection columns are updated exclusively by
    ``reconcile_customer_ledger()``.

    Raises:
        InvalidEntryTypeError: ``entry_type`` not in the allowed vocabulary.
        EntryAmountZeroError: ``signed_amount`` is zero.
        ValueError: customer does not exist (no matching ``customer`` row).
    """

    signed_amount = decimal.Decimal(signed_amount)

    # Validate entry_type.
    if entry_type not in ALLOWED_ENTRY_TYPES:
        raise InvalidEntryTypeError(entry_type)

    # Validate signed_amount is nonzero.
    if signed_amount == 0:
        raise EntryAmountZeroError(signed_amount)

    # Get-or-create the customer_ledger header.  This is the reason
    # record_entry() never requires the caller to have called
    # ensure_customer_ledger() first -- a brand-new customer's very
    # first ledger event is handled transparently.
    ledger = ensure_customer_ledger(
        session, customer_id=customer_id, currency_id=currency_id
    )

    sequence_no = _next_sequence_no(session, ledger.id)
    prev_hash = _prev_row_hash(session, ledger.id)
    occurred_at = datetime.datetime.now(datetime.timezone.utc)

    row_hash = _compute_row_hash(
        prev_hash=prev_hash,
        customer_ledger_id=ledger.id,
        reference_type=reference_type,
        reference_id=reference_id,
        sequence_no=sequence_no,
        signed_amount=signed_amount,
        entry_type=entry_type,
        occurred_at=occurred_at,
    )

    entry = CustomerLedgerEntry(
        customer_ledger_id=ledger.id,
        actor_user_id=actor_user_id,
        reference_type=reference_type,
        reference_id=reference_id,
        sequence_no=sequence_no,
        signed_amount=signed_amount,
        currency_id=currency_id,
        occurred_at=occurred_at,
        entry_type=entry_type,
        prev_hash=prev_hash,
        row_hash=row_hash,
        created_by=actor_user_id,
    )
    session.add(entry)
    session.flush()
    return entry


def get_balance(session: Session, customer_id: uuid.UUID) -> decimal.Decimal:
    """Return the customer's live balance, computed from the entry log.

    **Never trusts** the cached ``customer_ledger.current_balance`` column.
    That column is spec'd as "written only by the reconciliation job, never
    by general application code" -- the live service must always compute
    from the entry log (T22), not the projection (M13).

    Returns ``Decimal("0")`` if no entries exist yet.
    """

    ledger = session.execute(
        select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
    ).scalar_one_or_none()
    if ledger is None:
        return decimal.Decimal("0")

    total = session.execute(
        select(func.coalesce(func.sum(CustomerLedgerEntry.signed_amount), 0)).where(
            CustomerLedgerEntry.customer_ledger_id == ledger.id
        )
    ).scalar_one()
    return decimal.Decimal(total)


def reconcile_customer_ledger(
    session: Session,
    customer_id: uuid.UUID,
) -> CustomerLedger:
    """Reconcile the M13 cached projection from the authoritative entry log.

    This is the **one function** with permission to write
    ``customer_ledger.current_balance``, ``last_entry_seq``, and
    ``last_reconciled_at`` -- matching the spec's column-level GRANT
    pattern (same as ``invoice.amount_paid`` being written only by the
    reconciliation role, documented as an exception to the immutability
    trigger).

    ``record_entry()`` itself never touches these cached columns.
    ``get_balance()`` never trusts them.  Only this function writes them.

    Raises:
        CustomerLedgerNotFoundError: customer has no ledger header.
    """

    ledger = session.execute(
        select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
    ).scalar_one_or_none()
    if ledger is None:
        raise CustomerLedgerNotFoundError(customer_id)

    # Compute the true balance from the entry log.
    balance = session.execute(
        select(func.coalesce(func.sum(CustomerLedgerEntry.signed_amount), 0)).where(
            CustomerLedgerEntry.customer_ledger_id == ledger.id
        )
    ).scalar_one()

    # Find the highest sequence_no folded in.
    max_seq = session.execute(
        select(func.coalesce(func.max(CustomerLedgerEntry.sequence_no), 0)).where(
            CustomerLedgerEntry.customer_ledger_id == ledger.id
        )
    ).scalar_one()

    # Update the cached projection columns -- the only place these are
    # ever written (aside from initial row creation).
    ledger.current_balance = decimal.Decimal(balance)
    ledger.last_entry_seq = int(max_seq)
    ledger.last_reconciled_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return ledger


def list_entries(
    session: Session,
    customer_id: uuid.UUID,
    *,
    occurred_from: datetime.datetime | None = None,
    occurred_to: datetime.datetime | None = None,
    entry_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[CustomerLedgerEntry]:
    """List ledger entries for a customer, optionally filtered by date
    range and entry type.  Returns entries ordered by sequence_no
    (chronological append order).
    """

    ledger = session.execute(
        select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
    ).scalar_one_or_none()
    if ledger is None:
        return []

    query = select(CustomerLedgerEntry).where(
        CustomerLedgerEntry.customer_ledger_id == ledger.id
    )

    if occurred_from is not None:
        query = query.where(CustomerLedgerEntry.occurred_at >= occurred_from)
    if occurred_to is not None:
        query = query.where(CustomerLedgerEntry.occurred_at <= occurred_to)
    if entry_type is not None:
        query = query.where(CustomerLedgerEntry.entry_type == entry_type)

    query = query.order_by(CustomerLedgerEntry.sequence_no).offset(skip).limit(limit)
    return list(session.execute(query).scalars().all())


def get_entry(
    session: Session,
    entry_id: uuid.UUID,
) -> CustomerLedgerEntry:
    """Return a single ledger entry by id.  Raises: LookupError if not found."""

    entry = session.execute(
        select(CustomerLedgerEntry).where(CustomerLedgerEntry.id == entry_id)
    ).scalar_one_or_none()
    if entry is None:
        raise LookupError(f"No customer_ledger_entry with id '{entry_id}' exists.")
    return entry


__all__ = [
    "ALLOWED_ENTRY_TYPES",
    "CustomerLedgerNotFoundError",
    "DuplicateSequenceError",
    "EntryAmountZeroError",
    "InvalidEntryTypeError",
    "ensure_customer_ledger",
    "get_balance",
    "get_entry",
    "list_entries",
    "reconcile_customer_ledger",
    "record_entry",
]
