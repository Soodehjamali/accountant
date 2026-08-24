"""Service layer for the Reporting domain (M17 report_definition,
T26 report_run, H9 report_snapshot).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.

Scoped to ON-DEMAND report generation only.  No cron/scheduler
triggers ``run_report()`` automatically -- this codebase has no
scheduler infrastructure yet.  ``report_definition.schedule_cron``
is writable via the API (so it can be set now) but nothing
reads/acts on it yet.

Output format: this milestone only ever produces JSON (stored directly
in ``report_snapshot.snapshot_data``) with an optional flattened CSV
string alongside it.  If ``output_format`` is set to PDF or XLSX,
the report still runs and stores the JSON, but ``generated_document_id``
stays NULL -- binary rendering is not implemented yet.

Three report builders are implemented:
- ``AR_AGING``: one row per customer with a nonzero balance, bucketing
  outstanding ``INVOICE_ISSUED`` entries by age.
- ``INVENTORY_VALUATION``: one row per (warehouse, product) from
  ``inventory_balance_snapshot`` with ``quantity_on_hand > 0``.
- ``COMMISSION_PAYABLE``: one row per representative with commission
  (unconditional sum, matching the ledger's SUM projection pattern).

On any exception during report building, the run is marked FAILED
and the exception is re-raised, not swallowed.  ``report_run`` has
no ``error_message`` column -- the exception message is available
via the re-raised exception; this is noted as a schema gap.
"""

from __future__ import annotations

import csv
import datetime
import decimal
import io
import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models.commission_transaction import CommissionTransaction
from database.models.customer import Customer
from database.models.customer_ledger import CustomerLedger
from database.models.customer_ledger_entry import CustomerLedgerEntry
from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot
from database.models.inventory_transaction import InventoryTransaction
from database.models.representative import Representative
from database.models.report_definition import ReportDefinition
from database.models.report_run import ReportRun
from database.models.report_snapshot import ReportSnapshot
from database.models.report_type_ref import ReportTypeRef
from services import audit_service, customer_ledger_service


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Valid output_format values (app-level, no DB CHECK exists).
ALLOWED_OUTPUT_FORMATS = frozenset({"PDF", "CSV", "XLSX"})

#: The set of report_type_ref codes with implemented builders.
#: Keys are the ``report_type_ref.code`` values; values are the builder
#: callables.  Unrecognised codes produce a FAILED run.
REPORT_BUILDERS: dict[str, Callable[..., list[dict[str, Any]]]] = {}


def _register_builder(code: str):
    """Decorator to register a report builder by ``report_type_ref.code``."""

    def decorator(fn: Callable[..., list[dict[str, Any]]]) -> Callable[..., list[dict[str, Any]]]:
        REPORT_BUILDERS[code] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ReportDefinitionNotFoundError(LookupError):
    """Raised when a referenced report_definition_id has no matching row."""

    def __init__(self, report_definition_id: uuid.UUID) -> None:
        super().__init__(
            f"No report_definition with id '{report_definition_id}' exists."
        )
        self.report_definition_id = report_definition_id


class DuplicateReportDefinitionError(ValueError):
    """Raised when (owner_user_id, name) uniqueness is violated."""

    def __init__(self, owner_user_id: uuid.UUID, name: str) -> None:
        super().__init__(
            f"A report definition named '{name}' already exists for "
            f"owner '{owner_user_id}'."
        )
        self.owner_user_id = owner_user_id
        self.name = name


class ReportBuilderNotImplementedError(NotImplementedError):
    """Raised when no builder is registered for a report_type_ref.code."""

    def __init__(self, code: str) -> None:
        super().__init__(
            f"No report builder is implemented for report_type_ref "
            f"code '{code}'.  Available builders: "
            f"{', '.join(sorted(REPORT_BUILDERS.keys())) or '(none)'}."
        )
        self.code = code


class InvalidOutputFormatError(ValueError):
    """Raised when output_format is not in the allowed vocabulary."""

    def __init__(self, output_format: str) -> None:
        super().__init__(
            f"Invalid output_format '{output_format}'. "
            f"Must be one of: {', '.join(sorted(ALLOWED_OUTPUT_FORMATS))}."
        )
        self.output_format = output_format


class ReportRunFailedError(RuntimeError):
    """Raised when a report run fails during building."""

    def __init__(self, run_id: uuid.UUID, cause: Exception) -> None:
        super().__init__(
            f"Report run '{run_id}' failed: {cause}"
        )
        self.run_id = run_id
        self.cause = cause


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_report_definition_or_raise(
    session: Session, report_definition_id: uuid.UUID
) -> ReportDefinition:
    rd = session.execute(
        select(ReportDefinition).where(
            ReportDefinition.id == report_definition_id,
            ReportDefinition.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if rd is None:
        raise ReportDefinitionNotFoundError(report_definition_id)
    return rd


def _check_duplicate_definition(
    session: Session, owner_user_id: uuid.UUID, name: str
) -> None:
    """App-level uniqueness pre-check for uq_report_definition."""
    existing = session.execute(
        select(ReportDefinition).where(
            ReportDefinition.owner_user_id == owner_user_id,
            ReportDefinition.name == name,
            ReportDefinition.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateReportDefinitionError(owner_user_id, name)


def _flatten_to_csv(data: list[dict[str, Any]]) -> str:
    """Flatten a list of dicts to a CSV string."""
    if not data:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
    writer.writeheader()
    writer.writerows(data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Report Builders
# ---------------------------------------------------------------------------

@_register_builder("AR_AGING")
def _build_ar_aging(session: Session) -> list[dict[str, Any]]:
    """AR Aging report: one row per customer with a nonzero balance.

    Each outstanding ``INVOICE_ISSUED`` entry is aged independently
    from its ``occurred_at`` to now.  This is the simplest defensible
    version -- we do NOT attempt real FIFO invoice-to-payment matching
    (which would require tracking which payments offset which invoices).
    Each debit entry is bucketed by its own age, regardless of whether
    a payment has been received against the same invoice.

    Buckets: 0-30, 31-60, 61-90, 90+ days.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    rows: list[dict[str, Any]] = []

    # Get all customers with a ledger.
    ledgers = session.execute(
        select(CustomerLedger)
    ).scalars().all()

    for ledger in ledgers:
        balance = customer_ledger_service.get_balance(session, ledger.customer_id)
        if balance <= 0:
            continue

        # Get all INVOICE_ISSUED entries for this customer.
        entries = session.execute(
            select(CustomerLedgerEntry).where(
                CustomerLedgerEntry.customer_ledger_id == ledger.id,
                CustomerLedgerEntry.entry_type == "INVOICE_ISSUED",
            )
        ).scalars().all()

        buckets = {
            "0_30": decimal.Decimal("0"),
            "31_60": decimal.Decimal("0"),
            "61_90": decimal.Decimal("0"),
            "90_plus": decimal.Decimal("0"),
        }

        for entry in entries:
            age_days = (now - entry.occurred_at).days
            amount = decimal.Decimal(str(entry.signed_amount))
            if age_days <= 30:
                buckets["0_30"] += amount
            elif age_days <= 60:
                buckets["31_60"] += amount
            elif age_days <= 90:
                buckets["61_90"] += amount
            else:
                buckets["90_plus"] += amount

        # Fetch customer name.
        customer = session.get(Customer, ledger.customer_id)
        customer_name = customer.name if customer else str(ledger.customer_id)

        rows.append({
            "customer_id": str(ledger.customer_id),
            "customer_name": customer_name,
            "total_balance": str(balance),
            "0_30_days": str(buckets["0_30"]),
            "31_60_days": str(buckets["31_60"]),
            "61_90_days": str(buckets["61_90"]),
            "90_plus_days": str(buckets["90_plus"]),
        })

    return rows



@_register_builder("INVENTORY_VALUATION")
def _build_inventory_valuation(session: Session) -> list[dict[str, Any]]:
    """Inventory Valuation report: one row per (warehouse, product) with
    quantity_on_hand > 0, valued at the latest unit_cost from
    inventory_transaction.

    ``inventory_balance_snapshot`` does not carry a cost column -- cost
    is resolved from the latest ``inventory_transaction`` per
    (warehouse_id, product_id, lot_id).
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

    # Query inventory_balance_snapshot joined with latest cost.
    result = session.execute(
        select(
            InventoryBalanceSnapshot.warehouse_id,
            InventoryBalanceSnapshot.product_id,
            InventoryBalanceSnapshot.quantity_on_hand,
            latest_cost_sq,
        ).where(
            InventoryBalanceSnapshot.quantity_on_hand > 0
        )
    ).all()

    rows: list[dict[str, Any]] = []
    for warehouse_id, product_id, qty, unit_cost in result:
        unit_cost_dec = decimal.Decimal(str(unit_cost)) if unit_cost else decimal.Decimal("0")
        valued = qty * unit_cost_dec
        rows.append({
            "warehouse_id": str(warehouse_id),
            "product_id": str(product_id),
            "quantity_on_hand": str(qty),
            "unit_cost": str(unit_cost_dec),
            "total_value": str(valued),
        })

    return rows


@_register_builder("COMMISSION_PAYABLE")
def _build_commission_payable(session: Session) -> list[dict[str, Any]]:
    """Commission Payable report: one row per representative with commission.

    We sum ALL commission_transaction rows unconditionally (no state_event
    filter).  PAID/CLAWED_BACK events post as separate negative rows
    (per the CHECK constraint requiring CLAWED_BACK rows to have negative
    signed_amount), which an unconditional sum nets out correctly.  A
    state_event filter would incorrectly exclude these negative rows once
    services/commission_service.py eventually grows PAID/CLAWED_BACK-
    producing functions (it currently only has calculate_commission_for_order(),
    which only ever posts ACCRUED).

    Reuses the same query logic as ``kpi_snapshot_service
    ._compute_commission_payable()`` but broken out per representative.
    """
    result = session.execute(
        select(
            CommissionTransaction.representative_id,
            func.coalesce(func.sum(CommissionTransaction.signed_amount), 0).label("payable"),
        )
        .group_by(CommissionTransaction.representative_id)
    ).all()

    rows: list[dict[str, Any]] = []
    for rep_id, payable in result:
        rep = session.get(Representative, rep_id)
        rep_name = rep.person_name if rep else str(rep_id)
        rows.append({
            "representative_id": str(rep_id),
            "representative_name": rep_name,
            "payable_amount": str(payable),
        })

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_report_definition(
    session: Session,
    *,
    report_type_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    name: str,
    parameters: dict[str, Any],
    output_format: str = "PDF",
    schedule_cron: str | None = None,
    actor_id: uuid.UUID,
) -> ReportDefinition:
    """Create an M17 ``report_definition`` row.

    Validates ``output_format`` against the allowed vocabulary and
    enforces ``uq_report_definition (owner_user_id, name)`` with a
    clear error.

    ``schedule_cron`` is writable (so it can be set now) but nothing
    reads/acts on it yet -- no scheduler infrastructure exists.
    """
    if output_format not in ALLOWED_OUTPUT_FORMATS:
        raise InvalidOutputFormatError(output_format)

    _check_duplicate_definition(session, owner_user_id, name)

    rd = ReportDefinition(
        report_type_id=report_type_id,
        owner_user_id=owner_user_id,
        name=name,
        parameters=parameters,
        schedule_cron=schedule_cron,
        output_format=output_format,
        is_active=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(rd)
    session.flush()

    audit_service.record(
        session,
        entity_type="report_definition",
        entity_id=rd.id,
        action="CREATE",
        actor_user_id=actor_id,
        after={
            "name": name,
            "report_type_id": str(report_type_id),
            "output_format": output_format,
        },
    )
    session.flush()

    return rd


def get_report_definition(
    session: Session, report_definition_id: uuid.UUID
) -> ReportDefinition:
    """Return a single report definition.  Raises: ReportDefinitionNotFoundError."""
    return _get_report_definition_or_raise(session, report_definition_id)


def run_report(
    session: Session,
    *,
    report_definition_id: uuid.UUID,
    triggered_by: uuid.UUID | None,
) -> ReportRun:
    """Execute a report synchronously (QUEUED -> RUNNING -> COMPLETE|FAILED).

    Creates a T26 ``report_run`` row, dispatches to the appropriate
    builder based on ``report_type_ref.code``, and on success creates
    the H9 ``report_snapshot`` row (``uq_report_snapshot_run`` enforces
    one snapshot per run).

    On any exception during report building, the run is marked FAILED
    and the exception is re-raised, not swallowed.

    ``report_run`` has no ``error_message`` column -- the exception
    message is available via the re-raised exception.  This is noted
    as a schema gap.

    Synchronous execution: no background job queue exists in this
    codebase.
    """
    rd = _get_report_definition_or_raise(session, report_definition_id)

    # Resolve report_type_ref.code.
    report_type = session.execute(
        select(ReportTypeRef).where(ReportTypeRef.id == rd.report_type_id)
    ).scalar_one()

    now = datetime.datetime.now(datetime.timezone.utc)

    # Create the run row (QUEUED -> RUNNING in the same call).
    run = ReportRun(
        report_definition_id=rd.id,
        triggered_by=triggered_by,
        status="RUNNING",
        started_at=now,
        created_by=triggered_by,
        updated_by=triggered_by,
    )
    session.add(run)
    session.flush()

    # Dispatch to the builder.
    builder = REPORT_BUILDERS.get(report_type.code)
    if builder is None:
        # No builder for this code -- mark FAILED and re-raise.
        run.status = "FAILED"
        run.completed_at = datetime.datetime.now(datetime.timezone.utc)
        run.updated_by = triggered_by
        session.flush()

        exc = ReportBuilderNotImplementedError(report_type.code)
        raise exc

    try:
        data = builder(session)
        row_count = len(data)

        # Compute CSV if output_format is CSV.
        csv_data = None
        if rd.output_format == "CSV" and data:
            csv_data = _flatten_to_csv(data)

        # Build snapshot_data.
        snapshot_data: dict[str, Any] = {
            "report_type": report_type.code,
            "report_name": rd.name,
            "rows": data,
            "row_count": row_count,
        }
        if csv_data is not None:
            snapshot_data["csv"] = csv_data

        # Mark run COMPLETE.
        run.status = "COMPLETE"
        run.completed_at = datetime.datetime.now(datetime.timezone.utc)
        run.row_count = row_count
        run.updated_by = triggered_by
        session.flush()

        # Create the snapshot (H9).
        snapshot = ReportSnapshot(
            report_run_id=run.id,
            report_definition_id=rd.id,
            snapshot_data=snapshot_data,
            row_count=row_count,
            created_by=triggered_by,
        )
        session.add(snapshot)
        session.flush()

    except Exception as exc:
        # Mark the run FAILED.
        run.status = "FAILED"
        run.completed_at = datetime.datetime.now(datetime.timezone.utc)
        run.updated_by = triggered_by
        session.flush()

        # Re-raise -- not swallowed.  report_run has no error_message
        # column; this is a noted schema gap.
        raise ReportRunFailedError(run.id, exc) from exc

    return run


def get_report_run(
    session: Session, report_run_id: uuid.UUID
) -> ReportRun:
    """Return a single report run.  Raises: LookupError if not found."""
    run = session.execute(
        select(ReportRun).where(ReportRun.id == report_run_id)
    ).scalar_one_or_none()
    if run is None:
        raise LookupError(f"No report_run with id '{report_run_id}' exists.")
    return run


def get_report_snapshot(
    session: Session, report_run_id: uuid.UUID
) -> ReportSnapshot | None:
    """Return the snapshot for a completed report run, or None."""
    return session.execute(
        select(ReportSnapshot).where(ReportSnapshot.report_run_id == report_run_id)
    ).scalar_one_or_none()


__all__ = [
    "ALLOWED_OUTPUT_FORMATS",
    "DuplicateReportDefinitionError",
    "InvalidOutputFormatError",
    "REPORT_BUILDERS",
    "ReportBuilderNotImplementedError",
    "ReportDefinitionNotFoundError",
    "ReportRunFailedError",
    "create_report_definition",
    "get_report_definition",
    "get_report_run",
    "get_report_snapshot",
    "run_report",
]
