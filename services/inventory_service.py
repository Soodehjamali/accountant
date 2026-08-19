"""Service layer for the Inventory Ledger -- ``inventory_transaction`` (T1).

Per ``CLAUDE.md`` ("Inventory is always calculated from immutable
InventoryTransaction") and ``database/models/inventory_transaction.py``'s
own docstring, this module is the **sole sanctioned write path** onto
``inventory_transaction``. No endpoint or other service should construct
``InventoryTransaction`` rows directly.

Enforced here (none of this is enforced by a DB trigger -- the model's
docstring flags the sign-match / negative-balance triggers as an explicit
migration/DDL-level concern that was never actually added; see
``migrations/versions/20260817_0914_2b3846cb93c5_initial_schema.py``, which
declares only plain columns/constraints for this table, no trigger. All
three invariants below are therefore Python-layer, not DB-layer):

* ``signed_quantity``'s sign must match the posted movement type's
  ``sign`` convention (``MovementTypeSignMismatchError``).
* The resulting balance (sum of all non-reversed rows for the same
  warehouse/product/lot) must never go negative (``NegativeStockError``).
* The hash chain: each row's ``row_hash`` is a SHA-256 hex digest (64
  chars, matching ``HASH_HEX_LENGTH``) computed over the previous row's
  hash plus this row's own content, and ``prev_hash`` is the prior row's
  ``row_hash`` -- scoped per warehouse via ``sequence_no``, matching the
  table's own ``uq_inventory_transaction_seq (warehouse_id, sequence_no)``
  constraint.

RECONSTRUCTION NOTE: this file did not exist in the uploaded archive even
though ``backend/app/api/v1/endpoints/inventory.py`` imports it and
``backend/tests/test_inventory.py`` tests it in detail -- the FastAPI app
could not even start without it. It has been rebuilt to satisfy that
existing test file's exact contract (function names, keyword arguments,
and exception types) and the ``InventoryTransaction`` model as written.
The one point that is a genuine design call rather than a re-derivation:
a ``REVERSAL`` posting is exempted from the sign-match check, since a
reversal's quantity is the exact negation of whatever it reverses (either
sign), not a fixed convention -- please review this against your own
intended design before relying on it in production.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models.inventory_transaction import InventoryTransaction
from database.models.movement_type_ref import MovementTypeRef

#: Movement-type code used for system-generated reversal rows. Must exist
#: in ``movement_type_ref`` -- see ``services.bootstrap_service.ensure_movement_types``.
REVERSAL_MOVEMENT_TYPE_CODE = "REVERSAL"


class UnknownMovementTypeError(ValueError):
    """Raised when ``movement_type_code`` has no matching ``movement_type_ref`` row."""

    def __init__(self, code: str) -> None:
        super().__init__(f"No movement type with code '{code}' exists.")
        self.code = code


class MovementTypeSignMismatchError(ValueError):
    """Raised when ``signed_quantity``'s sign doesn't match the movement type's sign."""

    def __init__(self, code: str, expected_sign: int, signed_quantity: decimal.Decimal) -> None:
        super().__init__(
            f"signed_quantity {signed_quantity} does not match movement type "
            f"'{code}''s sign convention ({expected_sign:+d})."
        )
        self.code = code
        self.expected_sign = expected_sign


class NegativeStockError(ValueError):
    """Raised when posting a transaction would drive the projected balance negative."""

    def __init__(self, resulting_balance: decimal.Decimal) -> None:
        super().__init__(
            f"Posting this transaction would drive the balance negative "
            f"({resulting_balance})."
        )
        self.resulting_balance = resulting_balance


class TransactionNotFoundError(LookupError):
    """Raised when ``reverse_transaction`` is given an unknown transaction id."""

    def __init__(self, transaction_id: uuid.UUID) -> None:
        super().__init__(f"No inventory transaction with id '{transaction_id}' exists.")
        self.transaction_id = transaction_id


class AlreadyReversedError(ValueError):
    """Raised when attempting to reverse a transaction that was already reversed."""

    def __init__(self, transaction_id: uuid.UUID) -> None:
        super().__init__(f"Inventory transaction '{transaction_id}' was already reversed.")
        self.transaction_id = transaction_id


def _next_sequence_no(session: Session, warehouse_id: uuid.UUID) -> int:
    """Return the next monotonic ``sequence_no`` for this warehouse (1 if none yet)."""

    current_max = session.execute(
        select(func.max(InventoryTransaction.sequence_no)).where(
            InventoryTransaction.warehouse_id == warehouse_id
        )
    ).scalar_one()
    return (current_max or 0) + 1


def _prev_row_hash(session: Session, warehouse_id: uuid.UUID) -> str | None:
    """Return the ``row_hash`` of the latest row in this warehouse's chain, or ``None``."""

    return session.execute(
        select(InventoryTransaction.row_hash)
        .where(InventoryTransaction.warehouse_id == warehouse_id)
        .order_by(InventoryTransaction.sequence_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def _compute_row_hash(
    *,
    prev_hash: str | None,
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    sequence_no: int,
    signed_quantity: decimal.Decimal,
    unit_cost: decimal.Decimal,
    occurred_at: datetime.datetime,
) -> str:
    """SHA-256 hex digest chaining this row onto ``prev_hash``.

    SHA-256 (not invented here) is the hash already established elsewhere
    in this codebase -- ``database/naming.py``'s constraint-name truncation
    guard -- and its 64-char hex digest matches ``HASH_HEX_LENGTH`` /
    ``CHAR(64)`` on ``row_hash``/``prev_hash`` exactly.
    """

    payload = "|".join(
        str(part)
        for part in (
            prev_hash or "",
            warehouse_id,
            product_id,
            sequence_no,
            signed_quantity,
            unit_cost,
            occurred_at.isoformat(),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _current_balance(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    lot_id: uuid.UUID | None,
) -> decimal.Decimal:
    stmt = select(func.coalesce(func.sum(InventoryTransaction.signed_quantity), 0)).where(
        InventoryTransaction.warehouse_id == warehouse_id,
        InventoryTransaction.product_id == product_id,
        InventoryTransaction.is_reversed.is_(False),
    )
    if lot_id is not None:
        stmt = stmt.where(InventoryTransaction.lot_id == lot_id)
    return decimal.Decimal(session.execute(stmt).scalar_one())


def get_balance(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    lot_id: uuid.UUID | None = None,
) -> decimal.Decimal:
    """Return the current projected balance, always computed live from the ledger.

    If ``lot_id`` is omitted, the balance is aggregated across every lot
    for this (warehouse, product) pair -- matches the API layer's optional
    ``lot_id`` query parameter.
    """

    return _current_balance(session, warehouse_id=warehouse_id, product_id=product_id, lot_id=lot_id)


def post_transaction(
    session: Session,
    *,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    movement_type_code: str,
    signed_quantity: decimal.Decimal,
    unit_cost: decimal.Decimal,
    currency_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    lot_id: uuid.UUID | None = None,
    reason_code_id: uuid.UUID | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    _skip_sign_check: bool = False,
) -> InventoryTransaction:
    """Append one immutable row to the inventory ledger.

    Raises:
        UnknownMovementTypeError: ``movement_type_code`` has no catalog row.
        MovementTypeSignMismatchError: sign of ``signed_quantity`` doesn't
            match the movement type's sign convention.
        NegativeStockError: posting this row would drive the projected
            balance negative.
    """

    movement_type = session.execute(
        select(MovementTypeRef).where(MovementTypeRef.code == movement_type_code)
    ).scalar_one_or_none()
    if movement_type is None:
        raise UnknownMovementTypeError(movement_type_code)

    signed_quantity = decimal.Decimal(signed_quantity)
    if not _skip_sign_check:
        expected_sign = 1 if movement_type.sign > 0 else -1
        actual_sign = 1 if signed_quantity > 0 else -1
        if signed_quantity == 0 or actual_sign != expected_sign:
            raise MovementTypeSignMismatchError(movement_type_code, expected_sign, signed_quantity)

    resulting_balance = (
        _current_balance(session, warehouse_id=warehouse_id, product_id=product_id, lot_id=lot_id)
        + signed_quantity
    )
    if resulting_balance < 0:
        raise NegativeStockError(resulting_balance)

    sequence_no = _next_sequence_no(session, warehouse_id)
    prev_hash = _prev_row_hash(session, warehouse_id)
    occurred_at = datetime.datetime.now(datetime.timezone.utc)
    row_hash = _compute_row_hash(
        prev_hash=prev_hash,
        warehouse_id=warehouse_id,
        product_id=product_id,
        sequence_no=sequence_no,
        signed_quantity=signed_quantity,
        unit_cost=unit_cost,
        occurred_at=occurred_at,
    )

    transaction = InventoryTransaction(
        product_id=product_id,
        lot_id=lot_id,
        warehouse_id=warehouse_id,
        movement_type_id=movement_type.id,
        actor_user_id=actor_user_id,
        reason_code_id=reason_code_id,
        reference_type=reference_type,
        reference_id=reference_id,
        sequence_no=sequence_no,
        signed_quantity=signed_quantity,
        unit_cost=unit_cost,
        currency_id=currency_id,
        occurred_at=occurred_at,
        prev_hash=prev_hash,
        row_hash=row_hash,
        created_by=actor_user_id,
    )
    session.add(transaction)
    session.flush()
    return transaction


def reverse_transaction(
    session: Session,
    transaction_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    reason_code_id: uuid.UUID | None = None,
) -> InventoryTransaction:
    """Post a new REVERSAL row exactly negating ``transaction_id``.

    Never mutates or deletes the original row's content (append-only
    ledger) -- only flips its ``is_reversed`` flag so it can't be reversed
    twice. The REVERSAL row is exempted from the sign-match check (see
    module docstring): its quantity is the exact negation of the original,
    which may be either sign.

    Raises:
        TransactionNotFoundError: no row with this id exists.
        AlreadyReversedError: the row was already reversed once.
        NegativeStockError: the reversal itself would drive the balance
            negative (re-checked defensively, same as any other posting).
    """

    original = session.get(InventoryTransaction, transaction_id)
    if original is None:
        raise TransactionNotFoundError(transaction_id)
    if original.is_reversed:
        raise AlreadyReversedError(transaction_id)

    reversal = post_transaction(
        session,
        product_id=original.product_id,
        warehouse_id=original.warehouse_id,
        movement_type_code=REVERSAL_MOVEMENT_TYPE_CODE,
        signed_quantity=-decimal.Decimal(original.signed_quantity),
        unit_cost=original.unit_cost,
        currency_id=original.currency_id,
        actor_user_id=actor_user_id,
        lot_id=original.lot_id,
        reason_code_id=reason_code_id,
        reference_type="inventory_transaction",
        reference_id=original.id,
        _skip_sign_check=True,
    )
    reversal.reversal_of_id = original.id
    original.is_reversed = True
    session.add(original)
    session.flush()
    return reversal


__all__ = [
    "AlreadyReversedError",
    "MovementTypeSignMismatchError",
    "NegativeStockError",
    "REVERSAL_MOVEMENT_TYPE_CODE",
    "TransactionNotFoundError",
    "UnknownMovementTypeError",
    "get_balance",
    "post_transaction",
    "reverse_transaction",
]
