"""Service layer for the KPI Snapshot domain (H10 kpi_snapshot).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.

Key design decisions (per ``07_DATABASE_SPEC.md`` §H10):

* ``capture_kpi()`` appends one immutable ``kpi_snapshot`` row.  Scope
  consistency is validated at the application layer (``GLOBAL`` implies
  ``scope_id IS NULL``; ``WAREHOUSE``/``REPRESENTATIVE`` require
  ``scope_id`` set).  Uniqueness of ``(kpi_key, scope_type, scope_id,
  captured_at, period_granularity)`` is enforced as an app-level
  pre-check with a clear error, not just relying on the DB constraint.

* ``capture_global_kpis()`` computes and captures three GLOBAL-scope KPIs:
  - ``TOTAL_STOCK_VALUE``: sum of ``inventory_balance_snapshot.quantity_on_hand
    * latest_unit_cost`` across all warehouses.
  - ``AR_BALANCE``: sum of ``customer_ledger_service.get_balance()`` across
    all active customers.
  - ``COMMISSION_PAYABLE``: sum of all commission signed_amount
    (unconditional, matching the ledger's SUM projection pattern)
    from ``commission_transaction``.

* Per-warehouse / per-representative breakdowns (``scope_type=WAREHOUSE`` /
  ``REPRESENTATIVE``) are explicitly OUT OF SCOPE for this milestone --
  GLOBAL scope only.

* No cron/scheduler triggers ``capture_global_kpis()`` automatically --
  this codebase has no scheduler infrastructure yet.  The function is
  documented as intended to be called by a future scheduled job or manually
  via the endpoint.

Hash-chain / append-only pattern: same as ``inventory_service.py`` /
``customer_ledger_service.py`` -- rows are immutable once written.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.customer_ledger import CustomerLedger
from database.models.customer_ledger_entry import CustomerLedgerEntry
from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot
from database.models.inventory_transaction import InventoryTransaction
from database.models.kpi_snapshot import KpiSnapshot


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Valid scope_type values, matching ``ck_kpi_snapshot_scope_type``.
ALLOWED_SCOPE_TYPES = frozenset({"GLOBAL", "WAREHOUSE", "REPRESENTATIVE"})

#: Valid period_granularity values, matching ``ck_kpi_snapshot_granularity``.
ALLOWED_PERIOD_GRANULARITIES = frozenset({"DAILY", "WEEKLY", "MONTHLY"})

#: The three GLOBAL-scope KPI keys this milestone computes.
GLOBAL_KPI_KEYS = frozenset({
    "TOTAL_STOCK_VALUE",
    "AR_BALANCE",
    "COMMISSION_PAYABLE",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class InvalidScopeTypeError(ValueError):
    """Raised when ``scope_type`` is not in the allowed vocabulary."""

    def __init__(self, scope_type: str) -> None:
        super().__init__(
            f"Invalid scope_type '{scope_type}'. "
            f"Must be one of: {', '.join(sorted(ALLOWED_SCOPE_TYPES))}."
        )
        self.scope_type = scope_type


class InvalidPeriodGranularityError(ValueError):
    """Raised when ``period_granularity`` is not in the allowed vocabulary."""

    def __init__(self, period_granularity: str) -> None:
        super().__init__(
            f"Invalid period_granularity '{period_granularity}'. "
            f"Must be one of: {', '.join(sorted(ALLOWED_PERIOD_GRANULARITIES))}."
        )
        self.period_granularity = period_granularity


class ScopeConsistencyError(ValueError):
    """Raised when scope_type/scope_id violate the consistency CHECK."""

    def __init__(self, scope_type: str, scope_id: uuid.UUID | None) -> None:
        if scope_type == "GLOBAL":
            detail = (
                f"scope_type='GLOBAL' requires scope_id IS NULL, "
                f"got scope_id='{scope_id}'."
            )
        else:
            detail = (
                f"scope_type='{scope_type}' requires scope_id to be set, "
                f"got scope_id=None."
            )
        super().__init__(detail)
        self.scope_type = scope_type
        self.scope_id = scope_id


class DuplicateKpiSnapshotError(ValueError):
    """Raised when a KPI snapshot with the same uniqueness key already exists."""

    def __init__(
        self,
        kpi_key: str,
        scope_type: str,
        scope_id: uuid.UUID | None,
        captured_at: datetime.datetime,
        period_granularity: str,
    ) -> None:
        super().__init__(
            f"KPI snapshot already exists for "
            f"(kpi_key='{kpi_key}', scope_type='{scope_type}', "
            f"scope_id={scope_id}, captured_at={captured_at}, "
            f"period_granularity='{period_granularity}')."
        )
        self.kpi_key = kpi_key
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.captured_at = captured_at
        self.period_granularity = period_granularity


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_scope_consistency(
    scope_type: str, scope_id: uuid.UUID | None
) -> None:
    """Validate the ck_kpi_snapshot_scope_consistency CHECK at the app layer.

    ``scope_type='GLOBAL'`` implies ``scope_id IS NULL``;
    ``WAREHOUSE``/``REPRESENTATIVE`` require ``scope_id`` set.
    """
    if scope_type == "GLOBAL" and scope_id is not None:
        raise ScopeConsistencyError(scope_type, scope_id)
    if scope_type != "GLOBAL" and scope_id is None:
        raise ScopeConsistencyError(scope_type, scope_id)


def _check_duplicate(
    session: Session,
    *,
    kpi_key: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    captured_at: datetime.datetime,
    period_granularity: str,
) -> None:
    """App-level uniqueness pre-check for uq_kpi_snapshot."""
    query = (
        select(KpiSnapshot)
        .where(
            KpiSnapshot.kpi_key == kpi_key,
            KpiSnapshot.scope_type == scope_type,
            KpiSnapshot.scope_id == scope_id,
            KpiSnapshot.captured_at == captured_at,
            KpiSnapshot.period_granularity == period_granularity,
        )
        .limit(1)
    )
    existing = session.execute(query).scalar_one_or_none()
    if existing is not None:
        raise DuplicateKpiSnapshotError(
            kpi_key, scope_type, scope_id, captured_at, period_granularity
        )


# ---------------------------------------------------------------------------
# KPI computation helpers
# ---------------------------------------------------------------------------

def _compute_total_stock_value(session: Session) -> decimal.Decimal:
    """Compute TOTAL_STOCK_VALUE across all warehouses.

    ``inventory_balance_snapshot`` does not carry a ``unit_cost`` column --
    cost lives on ``inventory_transaction.unit_cost``.  We join each
    ``(warehouse_id, product_id, lot_id)`` in the snapshot to the latest
    transaction's ``unit_cost`` for the same key, then multiply by
    ``quantity_on_hand``.

    When no transaction exists for a snapshot row (edge case: snapshot
    seeded without a matching ledger row), that row contributes 0.
    """
    # Subquery: latest unit_cost per (warehouse, product, lot).
    # A scalar_subquery must select exactly one column -- the cost.
    # The warehouse/product/lot correlation is in the WHERE clause only.
    # NULL lot_id matching uses sa.or_ to treat NULL==NULL as TRUE.
    latest_cost_sq = (
        select(InventoryTransaction.unit_cost)
        .where(
            InventoryTransaction.warehouse_id == InventoryBalanceSnapshot.warehouse_id,
            InventoryTransaction.product_id == InventoryBalanceSnapshot.product_id,
            sa.or_(
                sa.and_(
                    InventoryTransaction.lot_id.is_(None),
                    InventoryBalanceSnapshot.lot_id.is_(None),
                ),
                InventoryTransaction.lot_id == InventoryBalanceSnapshot.lot_id,
            ),
        )
        .order_by(InventoryTransaction.occurred_at.desc())
        .correlate(InventoryBalanceSnapshot)
        .limit(1)
        .scalar_subquery()
    )

    total = session.execute(
        select(
            func.coalesce(
                func.sum(
                    InventoryBalanceSnapshot.quantity_on_hand
                    * latest_cost_sq
                ),
                0,
            )
        )
    ).scalar_one()

    return decimal.Decimal(total)


def _compute_ar_balance(session: Session) -> decimal.Decimal:
    """Compute AR_BALANCE across all active customers.

    Sums ``customer_ledger_entry.signed_amount`` for all customers whose
    ``customer.status = 'ACTIVE'``.  Inactive/deactivated customers are
    excluded because their outstanding balance should not count toward a
    live "how much are we owed" KPI.
    """
    total = session.execute(
        select(
            func.coalesce(
                func.sum(CustomerLedgerEntry.signed_amount),
                0,
            )
        )
        .join(CustomerLedger, CustomerLedger.id == CustomerLedgerEntry.customer_ledger_id)
        .join(Customer, Customer.id == CustomerLedger.customer_id)
        .where(Customer.status == "ACTIVE")
    ).scalar_one()

    return decimal.Decimal(total)


def _compute_commission_payable(session: Session) -> decimal.Decimal:
    """Compute COMMISSION_PAYABLE -- unconditional sum of signed_amount.

    We sum ALL commission_transaction rows unconditionally (no state_event
    filter).  PAID/CLAWED_BACK events post as separate negative rows
    (per the CHECK constraint requiring CLAWED_BACK rows to have negative
    signed_amount), which an unconditional sum nets out correctly.  A
    state_event filter would incorrectly exclude these negative rows once
    services/commission_service.py eventually grows PAID/CLAWED_BACK-
    producing functions (it currently only has calculate_commission_for_order(),
    which only ever posts ACCRUED).
    """
    total = session.execute(
        select(
            func.coalesce(
                func.sum(CommissionTransaction.signed_amount),
                0,
            )
        )
    ).scalar_one()

    return decimal.Decimal(total)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def capture_kpi(
    session: Session,
    *,
    kpi_key: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    value: decimal.Decimal,
    period_granularity: str,
    actor_user_id: uuid.UUID,
) -> KpiSnapshot:
    """Append one immutable ``kpi_snapshot`` row.

    Validates scope_type, period_granularity, scope consistency, and
    uniqueness before inserting.

    Args:
        kpi_key: e.g. ``TOTAL_STOCK_VALUE``, ``AR_BALANCE``.
        scope_type: ``GLOBAL``, ``WAREHOUSE``, or ``REPRESENTATIVE``.
        scope_id: Required for WAREHOUSE/REPRESENTATIVE, None for GLOBAL.
        value: The computed KPI value.
        period_granularity: ``DAILY``, ``WEEKLY``, or ``MONTHLY``.
        actor_user_id: The user triggering the capture.

    Raises:
        InvalidScopeTypeError: scope_type not in allowed vocabulary.
        InvalidPeriodGranularityError: period_granularity not in allowed vocabulary.
        ScopeConsistencyError: scope_type/scope_id mismatch.
        DuplicateKpiSnapshotError: uniqueness key collision.
    """
    if scope_type not in ALLOWED_SCOPE_TYPES:
        raise InvalidScopeTypeError(scope_type)
    if period_granularity not in ALLOWED_PERIOD_GRANULARITIES:
        raise InvalidPeriodGranularityError(period_granularity)

    _validate_scope_consistency(scope_type, scope_id)

    captured_at = datetime.datetime.now(datetime.timezone.utc)

    _check_duplicate(
        session,
        kpi_key=kpi_key,
        scope_type=scope_type,
        scope_id=scope_id,
        captured_at=captured_at,
        period_granularity=period_granularity,
    )

    # Set the denormalized FK columns per the spec's own column set.
    warehouse_id = scope_id if scope_type == "WAREHOUSE" else None
    representative_id = scope_id if scope_type == "REPRESENTATIVE" else None

    snapshot = KpiSnapshot(
        kpi_key=kpi_key,
        scope_type=scope_type,
        scope_id=scope_id,
        value=value,
        captured_at=captured_at,
        period_granularity=period_granularity,
        warehouse_id=warehouse_id,
        representative_id=representative_id,
        created_by=actor_user_id,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def capture_global_kpis(
    session: Session,
    *,
    period_granularity: str,
    actor_user_id: uuid.UUID,
) -> list[KpiSnapshot]:
    """Compute and capture the three GLOBAL-scope KPIs.

    This is the main entry point for the KPI snapshot job.  It computes:

    * ``TOTAL_STOCK_VALUE`` -- sum of ``quantity_on_hand * latest_unit_cost``
      across all warehouses.
    * ``AR_BALANCE`` -- sum of live customer balances across all active
      customers.
    * ``COMMISSION_PAYABLE`` -- unconditional sum of commission signed_amount.

    Each is captured as one ``GLOBAL``-scope ``kpi_snapshot`` row.

    **Per-warehouse / per-representative breakdowns are explicitly OUT OF
    SCOPE for this milestone** -- GLOBAL scope only.

    **No cron/scheduler triggers this automatically** -- this codebase has
    no scheduler infrastructure yet.  Intended to be called by a future
    scheduled job or manually via ``POST /kpi-snapshots/capture``.

    Returns the list of captured snapshots (one per KPI key).
    """
    if period_granularity not in ALLOWED_PERIOD_GRANULARITIES:
        raise InvalidPeriodGranularityError(period_granularity)

    results: list[KpiSnapshot] = []

    # TOTAL_STOCK_VALUE
    stock_value = _compute_total_stock_value(session)
    results.append(
        capture_kpi(
            session,
            kpi_key="TOTAL_STOCK_VALUE",
            scope_type="GLOBAL",
            scope_id=None,
            value=stock_value,
            period_granularity=period_granularity,
            actor_user_id=actor_user_id,
        )
    )

    # AR_BALANCE
    ar_balance = _compute_ar_balance(session)
    results.append(
        capture_kpi(
            session,
            kpi_key="AR_BALANCE",
            scope_type="GLOBAL",
            scope_id=None,
            value=ar_balance,
            period_granularity=period_granularity,
            actor_user_id=actor_user_id,
        )
    )

    # COMMISSION_PAYABLE
    commission_payable = _compute_commission_payable(session)
    results.append(
        capture_kpi(
            session,
            kpi_key="COMMISSION_PAYABLE",
            scope_type="GLOBAL",
            scope_id=None,
            value=commission_payable,
            period_granularity=period_granularity,
            actor_user_id=actor_user_id,
        )
    )

    return results


def get_latest_kpi(
    session: Session,
    kpi_key: str,
    *,
    scope_type: str = "GLOBAL",
    scope_id: uuid.UUID | None = None,
) -> KpiSnapshot | None:
    """Return the most recent captured row for that key/scope, or None."""
    query = (
        select(KpiSnapshot)
        .where(
            KpiSnapshot.kpi_key == kpi_key,
            KpiSnapshot.scope_type == scope_type,
            KpiSnapshot.scope_id == scope_id,
        )
        .order_by(KpiSnapshot.captured_at.desc())
        .limit(1)
    )
    return session.execute(query).scalar_one_or_none()


def list_kpi_history(
    session: Session,
    kpi_key: str,
    *,
    scope_type: str = "GLOBAL",
    scope_id: uuid.UUID | None = None,
    period_granularity: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[KpiSnapshot]:
    """Trend-chart read path for KPI history, ordered by captured_at DESC."""
    query = (
        select(KpiSnapshot)
        .where(
            KpiSnapshot.kpi_key == kpi_key,
            KpiSnapshot.scope_type == scope_type,
            KpiSnapshot.scope_id == scope_id,
        )
    )

    if period_granularity is not None:
        query = query.where(
            KpiSnapshot.period_granularity == period_granularity
        )

    query = (
        query.order_by(KpiSnapshot.captured_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(session.execute(query).scalars().all())


__all__ = [
    "ALLOWED_PERIOD_GRANULARITIES",
    "ALLOWED_SCOPE_TYPES",
    "DuplicateKpiSnapshotError",
    "GLOBAL_KPI_KEYS",
    "InvalidPeriodGranularityError",
    "InvalidScopeTypeError",
    "ScopeConsistencyError",
    "capture_global_kpis",
    "capture_kpi",
    "get_latest_kpi",
    "list_kpi_history",
]
