"""Service layer for the Commission domain (``commission_config`` C1 /
``commission_transaction`` T23).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.  Mirrors the structure already established
by ``services/invoice_service.py`` / ``services/order_service.py``.

Commission is configured via ``commission_config`` (C1) rows that map a
specificity-sorted combination of (representative, product_category,
order_type) to a rate.  ``resolve_commission_rate`` finds the best match
for a given order, falling back to progressively less specific configs
until the global default (all three fields NULL).

Commission is calculated at order COMPLETED time (PAID -> COMPLETED).
This is an explicit design assumption documented in the
``calculate_commission_for_order`` docstring: commission is definitive
only after the sale is finalized, not at shipment or invoicing.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.commission_config import CommissionConfig
from database.models.commission_transaction import CommissionTransaction
from database.models.order import Order
from services import audit_service

#: Permission code gating all commission mutations.
COMMISSION_MANAGE_PERMISSION_CODE = "COMMISSION_MANAGE"


class CommissionConfigNotFoundError(LookupError):
    """Raised when a referenced commission_config_id has no matching row."""

    def __init__(self, config_id: uuid.UUID) -> None:
        super().__init__(f"No commission config with id '{config_id}' exists.")
        self.config_id = config_id


class OrderNotCompletedError(ValueError):
    """Raised when commission is requested for an order not in COMPLETED state."""

    def __init__(self, order_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Order '{order_id}' is in state '{state}'; commission is only "
            "calculated for COMPLETED orders."
        )
        self.order_id = order_id
        self.state = state


class CommissionAlreadyCalculatedError(ValueError):
    """Raised when commission has already been calculated for an order."""

    def __init__(self, order_id: uuid.UUID) -> None:
        super().__init__(
            f"Commission has already been calculated for order '{order_id}'."
        )
        self.order_id = order_id


class NoCommissionConfigFoundError(LookupError):
    """Raised when no commission_config matches the given order parameters."""

    def __init__(
        self,
        representative_id: uuid.UUID | None,
        product_category_id: uuid.UUID | None,
        order_type: str,
    ) -> None:
        super().__init__(
            f"No commission config found for representative={representative_id}, "
            f"product_category={product_category_id}, order_type={order_type}."
        )
        self.representative_id = representative_id
        self.product_category_id = product_category_id
        self.order_type = order_type


def create_commission_config(
    session: Session,
    *,
    rate: decimal.Decimal,
    effective_from: datetime.datetime,
    effective_to: datetime.datetime | None = None,
    representative_id: uuid.UUID | None = None,
    product_category_id: uuid.UUID | None = None,
    order_type: str,
    actor_user_id: uuid.UUID,
) -> CommissionConfig:
    """Create a commission rate configuration.

    ``representative_id`` and ``product_category_id`` are nullable:
    NULL means "applies globally" (see ``database/models/commission_config.py``
    docstring).  ``order_type`` is required (LOCAL or DIRECT).

    Args:
        rate: Commission percentage (0..100).
        effective_from: Start of validity window.
        effective_to: End of validity window (None = open-ended).
        representative_id: Specific rep, or None for global.
        product_category_id: Specific category, or None for all.
        order_type: LOCAL or DIRECT.

    Raises:
        ValueError: if rate is out of [0, 100] range.
    """
    if rate < 0 or rate > 100:
        raise ValueError(f"Commission rate must be between 0 and 100, got {rate}.")

    if order_type not in ("LOCAL", "DIRECT"):
        raise ValueError(f"order_type must be LOCAL or DIRECT, got '{order_type}'.")

    config = CommissionConfig(
        representative_id=representative_id,
        product_category_id=product_category_id,
        order_type=order_type,
        rate=rate,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(config)
    session.flush()

    audit_service.record(
        session,
        entity_type="commission_config",
        entity_id=config.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        after={
            "rate": str(rate),
            "order_type": order_type,
            "representative_id": str(representative_id) if representative_id else None,
            "product_category_id": str(product_category_id) if product_category_id else None,
            "effective_from": effective_from.isoformat(),
        },
    )
    session.flush()

    return config


def list_commission_configs(session: Session) -> Iterable[CommissionConfig]:
    """Return all commission configurations."""
    return session.execute(
        select(CommissionConfig).order_by(CommissionConfig.effective_from.desc())
    ).scalars().all()


def resolve_commission_rate(
    session: Session,
    *,
    representative_id: uuid.UUID | None,
    order_type: str,
    effective_at: datetime.datetime | None = None,
) -> CommissionConfig:
    """Find the most specific matching ``commission_config`` for an order.

    **Matching specificity (most-specific first):**

    1. ``representative_id + order_type`` (NULL product_category)
    2. ``order_type`` alone (NULL representative, NULL product_category)
    3. Global default (NULL representative, NULL product_category, any order_type)

    Within the same specificity level, the most recently effective config
    (highest ``effective_from``) wins.

    ``effective_at`` defaults to ``now()`` if not provided.

    Raises:
        NoCommissionConfigFoundError: if no matching config exists.
    """
    now = effective_at or datetime.datetime.now(datetime.timezone.utc)

    # Strategy: try most specific first, fall back to less specific.
    # Each query filters to configs effective at the given time.
    base_filter = (
        (CommissionConfig.effective_from <= now)
        & (
            (CommissionConfig.effective_to.is_(None))
            | (CommissionConfig.effective_to >= now)
        )
    )

    # Level 1: representative + order_type (representative_id NOT NULL)
    if representative_id is not None:
        config = session.execute(
            select(CommissionConfig)
            .where(
                base_filter
                & (CommissionConfig.representative_id == representative_id)
                & (CommissionConfig.order_type == order_type)
            )
            .order_by(CommissionConfig.effective_from.desc())
            .limit(1)
        ).scalar_one_or_none()
        if config is not None:
            return config

    # Level 2: order_type only (representative_id IS NULL)
    config = session.execute(
        select(CommissionConfig)
        .where(
            base_filter
            & (CommissionConfig.representative_id.is_(None))
            & (CommissionConfig.order_type == order_type)
        )
        .order_by(CommissionConfig.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if config is not None:
        return config

    # Level 3: global fallback (any order_type, representative NULL)
    config = session.execute(
        select(CommissionConfig)
        .where(
            base_filter
            & (CommissionConfig.representative_id.is_(None))
        )
        .order_by(CommissionConfig.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if config is not None:
        return config

    raise NoCommissionConfigFoundError(representative_id, None, order_type)


def _next_sequence_no(session: Session, representative_id: uuid.UUID) -> int:
    """Return the next sequence_no for a representative's commission ledger."""
    max_seq = session.execute(
        select(CommissionTransaction.sequence_no)
        .where(CommissionTransaction.representative_id == representative_id)
        .order_by(CommissionTransaction.sequence_no.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (max_seq or 0) + 1


def calculate_commission_for_order(
    session: Session,
    *,
    order_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> CommissionTransaction:
    """Calculate and record a commission transaction for a COMPLETED order.

    Finds the applicable commission_config via ``resolve_commission_rate``
    and creates an ``ACCRUED`` ``commission_transaction`` (T23) row with:

    ``signed_amount = config.rate / 100 * order.grand_total``

    **Design assumption**: commission is calculated at order COMPLETED
    time (PAID -> COMPLETED), not at SHIPPED or INVOICED.  Commission is
    definitive only after the sale is finalized -- if the order is later
    returned or cancelled, a compensating ``CLAWED_BACK`` transaction
    (out of scope for this milestone) would reverse it.

    Raises:
        OrderNotCompletedError: order is not in COMPLETED state.
        CommissionAlreadyCalculatedError: commission already exists for this order.
        NoCommissionConfigFoundError: no matching commission config.
    """
    order = session.execute(
        select(Order).where(Order.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        from services.order_service import OrderNotFoundError
        raise OrderNotFoundError(order_id)

    if order.state != "COMPLETED":
        raise OrderNotCompletedError(order_id, order.state)

    # Idempotency: reject if commission already calculated for this order.
    existing = get_order_commission(session, order_id)
    if existing is not None:
        raise CommissionAlreadyCalculatedError(order_id)

    config = resolve_commission_rate(
        session,
        representative_id=order.representative_id,
        order_type=order.order_type,
    )

    signed_amount = (config.rate / 100) * decimal.Decimal(order.grand_total)

    txn = CommissionTransaction(
        representative_id=order.representative_id,
        order_id=order.id,
        commission_config_id=config.id,
        actor_user_id=actor_user_id,
        sequence_no=_next_sequence_no(session, order.representative_id),
        signed_amount=signed_amount,
        state_event="ACCRUED",
        rate_applied=config.rate,
        currency_id=order.currency_id,
    )
    session.add(txn)
    session.flush()

    audit_service.record(
        session,
        entity_type="commission_transaction",
        entity_id=txn.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        after={
            "order_id": str(order.id),
            "representative_id": str(order.representative_id),
            "signed_amount": str(signed_amount),
            "rate_applied": str(config.rate),
            "state_event": "ACCRUED",
        },
    )
    session.flush()

    return txn


def get_order_commission(
    session: Session, order_id: uuid.UUID
) -> CommissionTransaction | None:
    """Return the commission transaction for a given order, or None."""
    return session.execute(
        select(CommissionTransaction)
        .where(CommissionTransaction.order_id == order_id)
        .limit(1)
    ).scalar_one_or_none()


def get_commission_transaction(
    session: Session, transaction_id: uuid.UUID
) -> CommissionTransaction:
    """Return a single commission transaction by ID.

    Raises:
        CommissionTransactionNotFoundError: if no matching row.
    """
    txn = session.get(CommissionTransaction, transaction_id)
    if txn is None:
        raise CommissionTransactionNotFoundError(transaction_id)
    return txn


def list_commission_transactions(
    session: Session,
    *,
    representative_id: uuid.UUID | None = None,
    state_event: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[CommissionTransaction]:
    """List commission transactions with optional filters."""
    query = select(CommissionTransaction)
    if representative_id is not None:
        query = query.where(CommissionTransaction.representative_id == representative_id)
    if state_event is not None:
        query = query.where(CommissionTransaction.state_event == state_event)
    query = query.order_by(CommissionTransaction.occurred_at.desc()).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def approve_commission(
    session: Session,
    transaction_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CommissionTransaction:
    """Approve an ACCRUED commission transaction.

    Creates a new APPROVED row in the append-only commission ledger
    with the same ``signed_amount`` (positive).  The original ACCRUED
    row is not modified.

    Per SRS §10.3: "Commission status: ACCRUED -> APPROVED -> PAID."

    Raises:
        CommissionTransactionNotFoundError
        InvalidCommissionStateError: if state_event is not ACCRUED.
    """
    txn = get_commission_transaction(session, transaction_id)

    if txn.state_event != "ACCRUED":
        raise InvalidCommissionStateError(txn.state_event, "APPROVED")

    approved_txn = CommissionTransaction(
        representative_id=txn.representative_id,
        order_id=txn.order_id,
        commission_config_id=txn.commission_config_id,
        actor_user_id=actor_user_id,
        sequence_no=_next_sequence_no(session, txn.representative_id),
        signed_amount=txn.signed_amount,
        state_event="APPROVED",
        rate_applied=txn.rate_applied,
        currency_id=txn.currency_id,
    )
    session.add(approved_txn)
    session.flush()

    audit_service.record(
        session,
        entity_type="commission_transaction",
        entity_id=approved_txn.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        after={
            "action": "approve_commission",
            "original_txn_id": str(txn.id),
            "representative_id": str(txn.representative_id),
            "signed_amount": str(txn.signed_amount),
            "state_event": "APPROVED",
        },
    )
    session.flush()

    return approved_txn


def pay_commission(
    session: Session,
    transaction_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CommissionTransaction:
    """Pay an APPROVED commission transaction.

    Creates a new PAID row in the append-only commission ledger
    with the same ``signed_amount`` (positive).  The original row
    is not modified.

    Per SRS §10.3: "Commission status: ACCRUED -> APPROVED -> PAID."

    Raises:
        CommissionTransactionNotFoundError
        InvalidCommissionStateError: if state_event is not APPROVED.
    """
    txn = get_commission_transaction(session, transaction_id)

    if txn.state_event != "APPROVED":
        raise InvalidCommissionStateError(txn.state_event, "PAID")

    paid_txn = CommissionTransaction(
        representative_id=txn.representative_id,
        order_id=txn.order_id,
        commission_config_id=txn.commission_config_id,
        actor_user_id=actor_user_id,
        sequence_no=_next_sequence_no(session, txn.representative_id),
        signed_amount=txn.signed_amount,
        state_event="PAID",
        rate_applied=txn.rate_applied,
        currency_id=txn.currency_id,
    )
    session.add(paid_txn)
    session.flush()

    audit_service.record(
        session,
        entity_type="commission_transaction",
        entity_id=paid_txn.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        after={
            "action": "pay_commission",
            "original_txn_id": str(txn.id),
            "representative_id": str(txn.representative_id),
            "signed_amount": str(txn.signed_amount),
            "state_event": "PAID",
        },
    )
    session.flush()

    return paid_txn


def clawback_commission(
    session: Session,
    transaction_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> CommissionTransaction:
    """Clawback an ACCRUED or APPROVED commission transaction.

    Creates a new CLAWED_BACK row with a NEGATIVE ``signed_amount"
    (per DB CHECK: ``state_event = 'CLAWED_BACK' implies signed_amount < 0``).
    References the original transaction via ``reversal_of_id``.

    Per SRS §6.6/BR-R3: "Commission clawback on returned Scenario-B sales."
    Per SRS §10.3: Commission can be clawed back.

    Raises:
        CommissionTransactionNotFoundError
        InvalidCommissionStateError: if state_event is not ACCRUED or APPROVED.
    """
    txn = get_commission_transaction(session, transaction_id)

    if txn.state_event not in ("ACCRUED", "APPROVED"):
        raise InvalidCommissionStateError(txn.state_event, "CLAWED_BACK")

    clawback_txn = CommissionTransaction(
        representative_id=txn.representative_id,
        order_id=txn.order_id,
        commission_config_id=txn.commission_config_id,
        actor_user_id=actor_user_id,
        sequence_no=_next_sequence_no(session, txn.representative_id),
        signed_amount=-abs(txn.signed_amount),
        state_event="CLAWED_BACK",
        rate_applied=txn.rate_applied,
        currency_id=txn.currency_id,
        reversal_of_id=txn.id,
    )
    session.add(clawback_txn)
    session.flush()

    audit_service.record(
        session,
        entity_type="commission_transaction",
        entity_id=clawback_txn.id,
        action="CREATE",
        actor_user_id=actor_user_id,
        after={
            "action": "clawback_commission",
            "original_txn_id": str(txn.id),
            "representative_id": str(txn.representative_id),
            "signed_amount": str(-abs(txn.signed_amount)),
            "state_event": "CLAWED_BACK",
        },
    )
    session.flush()

    return clawback_txn


def get_representative_commission_balance(
    session: Session,
    representative_id: uuid.UUID,
) -> decimal.Decimal:
    """Return the net commission balance for a representative.

    Computed as SUM(signed_amount) across all commission_transaction
    rows for this representative.  This is the event-sourced balance
    (same pattern as customer_ledger and inventory_transaction).
    """
    result = session.execute(
        select(func.coalesce(func.sum(CommissionTransaction.signed_amount), 0))
        .where(CommissionTransaction.representative_id == representative_id)
    ).scalar_one()
    return decimal.Decimal(result)


__all__ = [
    "COMMISSION_MANAGE_PERMISSION_CODE",
    "CommissionAlreadyCalculatedError",
    "CommissionConfigNotFoundError",
    "NoCommissionConfigFoundError",
    "OrderNotCompletedError",
    "calculate_commission_for_order",
    "create_commission_config",
    "get_order_commission",
    "list_commission_configs",
    "resolve_commission_rate",
]
