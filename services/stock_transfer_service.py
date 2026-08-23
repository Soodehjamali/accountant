"""Service layer for the Stock Transfer aggregate (``stock_transfer`` T4 /
``transfer_line`` T5 / ``transfer_history`` T6).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job. Mirrors the structure already established
by ``services/order_service.py`` / ``services/invoice_service.py``.

State machine: implements the graph derived from ``09_Decisions.md``
ADR-005 (two-phase: dispatch debits source warehouse; receive credits
destination warehouse) combined with ``07_DATABASE_SPEC.md`` §T4's
``TransferState`` vocabulary.

ADR-005 key decisions applied here:
* ``dispatch_transfer`` (DRAFT -> DISPATCHED) posts a ``TRANSFER_OUT``
  inventory transaction against the source warehouse.
* ``receive_transfer`` (DISPATCHED -> RECEIVED) posts a ``TRANSFER_IN``
  inventory transaction against the destination warehouse.
* The source warehouse is debited at dispatch time; the destination
  warehouse is credited at receive time -- not before, not simultaneously.

Every state transition writes a ``transfer_history`` row -- see
``_transition`` below, the single choke point every state-changing
function funnels through.

States present in the DB CHECK vocabulary (§T4) but omitted from
``ALLOWED_TRANSITIONS`` above because no service function transitions
through them: PENDING, APPROVED, IN_TRANSIT, PARTIAL_RECEIVED, CLOSED.
A future milestone can add ``submit_transfer`` (DRAFT -> PENDING) and
``approve_transfer`` (PENDING -> APPROVED -> DISPATCHED) if the
business requires a formal approval workflow.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.stock_transfer import StockTransfer
from database.models.transfer_line import TransferLine
from database.models.transfer_history import TransferHistory
from database.models.warehouse import Warehouse
from services import audit_service, inventory_service

#: Permission code gating all stock-transfer mutations.
TRANSFER_MANAGE_PERMISSION_CODE = "TRANSFER_MANAGE"

#: The accepted Stock Transfer state graph.  Keys are the "from" state;
#: values are the set of states directly reachable from it.
#: Derived from ADR-005's two-phase model: DRAFT -> DISPATCHED -> RECEIVED.
#: Unused intermediate states (PENDING, APPROVED, IN_TRANSIT, PARTIAL_RECEIVED,
#: CLOSED) from the spec's 9-state vocabulary are omitted because no service
#: function transitions through them.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"DISPATCHED", "CANCELLED"}),
    "DISPATCHED": frozenset({"RECEIVED", "CANCELLED"}),
    "RECEIVED": frozenset(),
    "CANCELLED": frozenset(),
}


class TransferNotFoundError(LookupError):
    """Raised when a referenced ``transfer_id`` has no matching row."""

    def __init__(self, transfer_id: uuid.UUID) -> None:
        super().__init__(f"No stock transfer with id '{transfer_id}' exists.")
        self.transfer_id = transfer_id


class WarehouseNotFoundError(LookupError):
    """Raised when a referenced ``warehouse_id`` has no matching row."""

    def __init__(self, warehouse_id: uuid.UUID) -> None:
        super().__init__(f"No warehouse with id '{warehouse_id}' exists.")
        self.warehouse_id = warehouse_id


class SameWarehouseError(ValueError):
    """Raised when source and destination warehouses are the same."""

    def __init__(self) -> None:
        super().__init__("Source and destination warehouses must be different.")


class EmptyTransferError(ValueError):
    """Raised when ``create_transfer`` is called with zero lines."""

    def __init__(self) -> None:
        super().__init__("A transfer must have at least one line.")


class InvalidTransferStateTransitionError(ValueError):
    """Raised when a transition isn't a valid edge in ``ALLOWED_TRANSITIONS``."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(
            f"Cannot transition a stock transfer from '{from_state}' to '{to_state}'."
        )
        self.from_state = from_state
        self.to_state = to_state


class TransferNotCancellableError(ValueError):
    """Raised when attempting to cancel a transfer that is not in DRAFT."""

    def __init__(self, state: str) -> None:
        super().__init__(
            f"Transfers can only be cancelled from DRAFT state; current state is '{state}'."
        )
        self.state = state


def _get_transfer_or_raise(session: Session, transfer_id: uuid.UUID) -> StockTransfer:
    transfer = session.execute(
        select(StockTransfer).where(
            StockTransfer.id == transfer_id, StockTransfer.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if transfer is None:
        raise TransferNotFoundError(transfer_id)
    return transfer


def _generate_transfer_number() -> str:
    """A simple, collision-safe business key: date-stamped + random suffix."""
    today = datetime.date.today().strftime("%Y%m%d")
    return f"TRF-{today}-{uuid.uuid4().hex[:8].upper()}"


def _transition(
    session: Session,
    transfer: StockTransfer,
    to_state: str,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> StockTransfer:
    """The single choke point every state-changing function funnels through:
    validates the edge against ``ALLOWED_TRANSITIONS``, applies it, and
    writes the matching ``transfer_history`` row.

    Raises:
        InvalidTransferStateTransitionError: not a valid edge.
    """
    from_state = transfer.state
    if to_state not in ALLOWED_TRANSITIONS.get(from_state, frozenset()):
        raise InvalidTransferStateTransitionError(from_state, to_state)

    transfer.state = to_state
    transfer.updated_by = actor_user_id
    session.add(
        TransferHistory(
            stock_transfer_id=transfer.id,
            actor_user_id=actor_user_id,
            from_state=from_state,
            to_state=to_state,
            note=note,
        )
    )
    session.flush()

    audit_service.record(
        session,
        entity_type="stock_transfer",
        entity_id=transfer.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": from_state},
        after={"state": to_state, "note": note},
    )
    session.flush()
    return transfer


class TransferLineInput:
    """Plain input bundle for one line of ``create_transfer``.  Not an ORM model."""

    def __init__(
        self,
        *,
        product_id: uuid.UUID,
        qty_requested: decimal.Decimal,
        unit_cost: decimal.Decimal,
        lot_id: uuid.UUID | None = None,
    ) -> None:
        self.product_id = product_id
        self.qty_requested = decimal.Decimal(qty_requested)
        self.unit_cost = decimal.Decimal(unit_cost)
        self.lot_id = lot_id


def _get_currency_for_transfer(
    session: Session, source_warehouse_id: uuid.UUID
) -> uuid.UUID:
    """Return a currency_id suitable for inventory postings on this transfer.

    Looks up an existing inventory_transaction at the source warehouse to
    find the currency in use.  Falls back to the default IRR currency if
    no inventory exists yet (same bootstrap the test fixtures rely on).

    This is a pragmatic simplification -- the Transfer model itself does
    not carry a currency_id (per the spec's §T4 column list), but
    ``inventory_service.post_transaction`` requires one.  A full
    implementation would derive currency from the product's price-list or
    from a transfer-level currency override.
    """
    from database.models.inventory_transaction import InventoryTransaction
    from database.models.currency import Currency

    existing_currency = session.execute(
        select(InventoryTransaction.currency_id)
        .where(InventoryTransaction.warehouse_id == source_warehouse_id)
        .limit(1)
    ).scalar_one_or_none()
    if existing_currency is not None:
        return existing_currency

    # Fallback: use the default IRR currency (bootstrap creates it).
    default_currency = session.execute(
        select(Currency).where(Currency.code == "IRR")
    ).scalar_one_or_none()
    if default_currency is not None:
        return default_currency.id

    # Last resort: first currency in the catalog.
    first_currency = session.execute(select(Currency).limit(1)).scalar_one_or_none()
    if first_currency is not None:
        return first_currency.id

    raise ValueError("No currency found in the database for inventory posting.")


def create_transfer(
    session: Session,
    *,
    source_warehouse_id: uuid.UUID,
    destination_warehouse_id: uuid.UUID,
    lines: Iterable[TransferLineInput],
    requested_by: uuid.UUID,
    ownership_mode_snapshot: str = "OWNED",
    note: str | None = None,
) -> StockTransfer:
    """Create a new ``DRAFT`` stock transfer with its lines.

    Raises:
        WarehouseNotFoundError: source or destination warehouse not found.
        SameWarehouseError: source and destination are the same.
        EmptyTransferError: ``lines`` is empty.
    """
    lines = list(lines)
    if not lines:
        raise EmptyTransferError()

    if source_warehouse_id == destination_warehouse_id:
        raise SameWarehouseError()

    source_wh = session.get(Warehouse, source_warehouse_id)
    if source_wh is None:
        raise WarehouseNotFoundError(source_warehouse_id)

    dest_wh = session.get(Warehouse, destination_warehouse_id)
    if dest_wh is None:
        raise WarehouseNotFoundError(destination_warehouse_id)

    # Resolve currency for future inventory postings (not stored on
    # the Transfer model per spec, but needed by post_transaction).
    currency_id = _get_currency_for_transfer(session, source_warehouse_id)

    transfer = StockTransfer(
        transfer_number=_generate_transfer_number(),
        source_warehouse_id=source_warehouse_id,
        destination_warehouse_id=destination_warehouse_id,
        state="DRAFT",
        requested_by=requested_by,
        ownership_mode_snapshot=ownership_mode_snapshot,
        created_by=requested_by,
        updated_by=requested_by,
    )
    session.add(transfer)
    session.flush()

    for line_in in lines:
        session.add(
            TransferLine(
                stock_transfer_id=transfer.id,
                product_id=line_in.product_id,
                lot_id=line_in.lot_id,
                qty_requested=line_in.qty_requested,
                unit_cost=line_in.unit_cost,
                created_by=requested_by,
                updated_by=requested_by,
            )
        )
    session.flush()

    # Store the resolved currency_id on the transfer for later use
    # by dispatch/receive (not a spec column -- stored transiently).
    transfer._resolved_currency_id = currency_id  # type: ignore[attr-defined]

    # Record creation history (DRAFT -> DRAFT, for traceability).
    session.add(
        TransferHistory(
            stock_transfer_id=transfer.id,
            actor_user_id=requested_by,
            from_state="DRAFT",
            to_state="DRAFT",
            note=note or "Transfer created",
        )
    )
    session.flush()

    audit_service.record(
        session,
        entity_type="stock_transfer",
        entity_id=transfer.id,
        action="CREATE",
        actor_user_id=requested_by,
        after={
            "transfer_number": transfer.transfer_number,
            "source_warehouse_id": str(source_warehouse_id),
            "destination_warehouse_id": str(destination_warehouse_id),
            "state": "DRAFT",
        },
    )
    session.flush()

    return transfer


def get_transfer(session: Session, transfer_id: uuid.UUID) -> StockTransfer:
    """Return a single, non-deleted stock transfer. Raises: TransferNotFoundError."""
    return _get_transfer_or_raise(session, transfer_id)


def list_transfers(
    session: Session,
    *,
    source_warehouse_id: uuid.UUID | None = None,
    destination_warehouse_id: uuid.UUID | None = None,
    state: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[StockTransfer]:
    """List non-deleted stock transfers, optionally filtered."""
    query = select(StockTransfer).where(StockTransfer.deleted_at.is_(None))
    if source_warehouse_id is not None:
        query = query.where(StockTransfer.source_warehouse_id == source_warehouse_id)
    if destination_warehouse_id is not None:
        query = query.where(StockTransfer.destination_warehouse_id == destination_warehouse_id)
    if state is not None:
        query = query.where(StockTransfer.state == state)
    query = query.order_by(StockTransfer.requested_at.desc()).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def list_transfer_lines(session: Session, transfer_id: uuid.UUID) -> Iterable[TransferLine]:
    """Return all lines for a stock transfer. Raises: TransferNotFoundError."""
    _get_transfer_or_raise(session, transfer_id)
    return session.execute(
        select(TransferLine)
        .where(TransferLine.stock_transfer_id == transfer_id)
        .order_by(TransferLine.created_at)
    ).scalars().all()


def get_transfer_history(session: Session, transfer_id: uuid.UUID) -> Iterable[TransferHistory]:
    """Return the state-change history for a stock transfer. Raises: TransferNotFoundError."""
    _get_transfer_or_raise(session, transfer_id)
    return session.execute(
        select(TransferHistory)
        .where(TransferHistory.stock_transfer_id == transfer_id)
        .order_by(TransferHistory.event_at)
    ).scalars().all()


def dispatch_transfer(
    session: Session,
    transfer_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> StockTransfer:
    """``DRAFT -> DISPATCHED``.

    Posts a ``TRANSFER_OUT`` inventory transaction against the source
    warehouse for every line, per ADR-005's two-phase model: the source
    warehouse is debited at dispatch time, not at receive time.

    Raises:
        TransferNotFoundError, InvalidTransferStateTransitionError.
    """
    transfer = _get_transfer_or_raise(session, transfer_id)

    lines = session.execute(
        select(TransferLine).where(TransferLine.stock_transfer_id == transfer_id)
    ).scalars().all()

    # Resolve currency for inventory posting.
    currency_id = getattr(transfer, '_resolved_currency_id', None)
    if currency_id is None:
        currency_id = _get_currency_for_transfer(session, transfer.source_warehouse_id)

    for line in lines:
        inventory_service.post_transaction(
            session,
            product_id=line.product_id,
            warehouse_id=transfer.source_warehouse_id,
            movement_type_code="TRANSFER_OUT",
            signed_quantity=-line.qty_requested,
            unit_cost=line.unit_cost,
            currency_id=currency_id,
            actor_user_id=actor_user_id,
            lot_id=line.lot_id,
            reference_type="stock_transfer",
            reference_id=transfer.id,
        )
        # Update qty_dispatched on the line.
        line.qty_dispatched = line.qty_requested
        line.updated_by = actor_user_id
    session.flush()

    now = datetime.datetime.now(datetime.timezone.utc)
    transfer.dispatched_at = now
    transfer.updated_by = actor_user_id
    session.flush()

    return _transition(session, transfer, "DISPATCHED", actor_user_id=actor_user_id, note=note)


def receive_transfer(
    session: Session,
    transfer_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> StockTransfer:
    """``DISPATCHED -> RECEIVED``.

    Posts a ``TRANSFER_IN`` inventory transaction against the destination
    warehouse for every line, per ADR-005's two-phase model: the
    destination warehouse is credited at receive time.

    Raises:
        TransferNotFoundError, InvalidTransferStateTransitionError.
    """
    transfer = _get_transfer_or_raise(session, transfer_id)

    lines = session.execute(
        select(TransferLine).where(TransferLine.stock_transfer_id == transfer_id)
    ).scalars().all()

    # Resolve currency for inventory posting.
    currency_id = getattr(transfer, '_resolved_currency_id', None)
    if currency_id is None:
        currency_id = _get_currency_for_transfer(session, transfer.source_warehouse_id)

    for line in lines:
        inventory_service.post_transaction(
            session,
            product_id=line.product_id,
            warehouse_id=transfer.destination_warehouse_id,
            movement_type_code="TRANSFER_IN",
            signed_quantity=line.qty_requested,
            unit_cost=line.unit_cost,
            currency_id=currency_id,
            actor_user_id=actor_user_id,
            lot_id=line.lot_id,
            reference_type="stock_transfer",
            reference_id=transfer.id,
        )
        # Update qty_received and qty_variance on the line.
        line.qty_received = line.qty_requested
        line.qty_variance = line.qty_dispatched - line.qty_received
        line.updated_by = actor_user_id
    session.flush()

    now = datetime.datetime.now(datetime.timezone.utc)
    transfer.received_at = now
    transfer.updated_by = actor_user_id
    session.flush()

    return _transition(session, transfer, "RECEIVED", actor_user_id=actor_user_id, note=note)


def cancel_transfer(
    session: Session,
    transfer_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> StockTransfer:
    """Cancel a stock transfer.

    Per the spec, cancellation is only permitted from DRAFT.  Post-DRAFT
    transfers are not cancelled through this path -- they progress
    through the state machine or require a separate reversal process.

    Raises:
        TransferNotFoundError, TransferNotCancellableError.
    """
    transfer = _get_transfer_or_raise(session, transfer_id)
    if transfer.state != "DRAFT":
        raise TransferNotCancellableError(transfer.state)
    return _transition(session, transfer, "CANCELLED", actor_user_id=actor_user_id, note=note)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "TRANSFER_MANAGE_PERMISSION_CODE",
    "EmptyTransferError",
    "InvalidTransferStateTransitionError",
    "SameWarehouseError",
    "TransferLineInput",
    "TransferNotFoundError",
    "TransferNotCancellableError",
    "WarehouseNotFoundError",
    "cancel_transfer",
    "create_transfer",
    "dispatch_transfer",
    "get_transfer",
    "get_transfer_history",
    "list_transfer_lines",
    "list_transfers",
    "receive_transfer",
]
