"""Service layer for the Warehouse aggregate (M4) and WarehouseAssignment (C5).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.

Business constraints (service-layer only):
- Exactly ONE type=FACTORY active at a time (DB partial unique index).
- Cannot deactivate a warehouse holding non-zero stock (cross-table).
- ownership_mode immutable once stock exists (cross-table).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment


class DuplicateWarehouseCodeError(ValueError):
    """Raised when ``create_warehouse`` is called with a ``code`` already in use."""

    def __init__(self, code: str) -> None:
        super().__init__(f"A warehouse with code '{code}' already exists.")
        self.code = code


class WarehouseNotFoundError(LookupError):
    """Raised when a referenced ``warehouse_id`` has no matching row."""

    def __init__(self, warehouse_id: uuid.UUID) -> None:
        super().__init__(f"No warehouse with id '{warehouse_id}' exists.")
        self.warehouse_id = warehouse_id


class WarehouseNotDeactivatableError(ValueError):
    """Raised when attempting to deactivate a warehouse with active stock."""

    def __init__(self, warehouse_id: uuid.UUID) -> None:
        super().__init__(
            f"Warehouse '{warehouse_id}' cannot be deactivated: "
            "active stock remains."
        )
        self.warehouse_id = warehouse_id


class DuplicateAssignmentError(ValueError):
    """Raised when creating a warehouse assignment that already exists."""

    def __init__(self, representative_id: uuid.UUID, warehouse_id: uuid.UUID) -> None:
        super().__init__(
            f"Representative '{representative_id}' is already assigned to warehouse '{warehouse_id}'."
        )
        self.representative_id = representative_id
        self.warehouse_id = warehouse_id


class AssignmentNotFoundError(LookupError):
    """Raised when a warehouse assignment does not exist."""

    def __init__(self, representative_id: uuid.UUID, warehouse_id: uuid.UUID) -> None:
        super().__init__(
            f"No assignment found for representative '{representative_id}' "
            f"and warehouse '{warehouse_id}'."
        )
        self.representative_id = representative_id
        self.warehouse_id = warehouse_id


class WarehouseInUseError(ValueError):
    """Raised when attempting to delete a warehouse that is still referenced."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------------------
# Warehouse CRUD
# ---------------------------------------------------------------------------

def _get_warehouse_or_raise(session: Session, warehouse_id: uuid.UUID) -> Warehouse:
    wh = session.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if wh is None:
        raise WarehouseNotFoundError(warehouse_id)
    return wh


def create_warehouse(
    session: Session,
    *,
    code: str,
    name: str,
    type: str,
    ownership_mode: str,
    created_by: uuid.UUID,
    address: str | None = None,
    city_ref_id: uuid.UUID | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    responsible_user_id: uuid.UUID | None = None,
) -> Warehouse:
    """Create and return a new Warehouse, defaulting status to ACTIVE.

    Raises:
        DuplicateWarehouseCodeError: if ``code`` is already taken.
    """
    existing = session.execute(
        select(Warehouse).where(Warehouse.code == code)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateWarehouseCodeError(code)

    wh = Warehouse(
        code=code,
        name=name,
        type=type,
        ownership_mode=ownership_mode,
        address=address,
        city_ref_id=city_ref_id,
        latitude=latitude,
        longitude=longitude,
        responsible_user_id=responsible_user_id,
        status="ACTIVE",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(wh)
    session.flush()
    return wh


def get_warehouse(session: Session, warehouse_id: uuid.UUID) -> Warehouse:
    """Return a single warehouse. Raises: WarehouseNotFoundError."""
    return _get_warehouse_or_raise(session, warehouse_id)


def list_warehouses(
    session: Session,
    *,
    type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[Warehouse]:
    """List non-deleted warehouses, optionally filtered."""
    query = select(Warehouse).where(Warehouse.deleted_at.is_(None))
    if type is not None:
        query = query.where(Warehouse.type == type)
    if status is not None:
        query = query.where(Warehouse.status == status)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Warehouse.name.ilike(pattern) | Warehouse.code.ilike(pattern)
        )
    query = query.order_by(Warehouse.name).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def update_warehouse(
    session: Session,
    warehouse_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
    name: str | None = None,
    address: str | None = None,
    city_ref_id: uuid.UUID | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    responsible_user_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Warehouse:
    """Update an existing warehouse.

    Raises: WarehouseNotFoundError.

    Note: ``type`` and ``ownership_mode`` are NOT updatable here --
    ``ownership_mode`` is immutable once stock exists (business constraint),
    and ``type`` is a structural property.
    """
    wh = _get_warehouse_or_raise(session, warehouse_id)

    if name is not None:
        wh.name = name
    if address is not None:
        wh.address = address
    if city_ref_id is not None:
        wh.city_ref_id = city_ref_id
    if latitude is not None:
        wh.latitude = latitude
    if longitude is not None:
        wh.longitude = longitude
    if responsible_user_id is not None:
        wh.responsible_user_id = responsible_user_id
    if status is not None:
        wh.status = status
    wh.updated_by = updated_by
    session.flush()
    return wh


def deactivate_warehouse(
    session: Session,
    warehouse_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
) -> Warehouse:
    """Soft-delete a warehouse (status -> INACTIVE, deleted_at set).

    Raises:
        WarehouseNotFoundError.
        WarehouseNotDeactivatableError: if the warehouse has non-zero stock.
    """
    wh = _get_warehouse_or_raise(session, warehouse_id)

    # Business constraint: cannot deactivate if warehouse has non-zero stock.
    from database.models.inventory_transaction import InventoryTransaction
    from sqlalchemy import func

    balance = session.execute(
        select(func.coalesce(func.sum(InventoryTransaction.signed_quantity), 0)).where(
            InventoryTransaction.warehouse_id == warehouse_id,
        )
    ).scalar_one()

    if decimal.Decimal(str(balance)) > 0:
        raise WarehouseNotDeactivatableError(warehouse_id)

    wh.status = "INACTIVE"
    wh.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    wh.updated_by = updated_by
    session.flush()
    return wh


# ---------------------------------------------------------------------------
# Warehouse Assignment CRUD
# ---------------------------------------------------------------------------

def create_assignment(
    session: Session,
    *,
    representative_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    created_by: uuid.UUID,
    is_primary: bool = False,
    effective_from: datetime.datetime | None = None,
    effective_to: datetime.datetime | None = None,
) -> WarehouseAssignment:
    """Assign a representative to a warehouse.

    Raises:
        DuplicateAssignmentError: if the assignment already exists.
        WarehouseNotFoundError: if warehouse does not exist.
    """
    # Validate warehouse exists.
    _get_warehouse_or_raise(session, warehouse_id)

    # Check for existing assignment.
    existing = session.execute(
        select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == representative_id,
            WarehouseAssignment.warehouse_id == warehouse_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateAssignmentError(representative_id, warehouse_id)

    if effective_from is None:
        effective_from = datetime.datetime.now(datetime.timezone.utc)

    assignment = WarehouseAssignment(
        representative_id=representative_id,
        warehouse_id=warehouse_id,
        is_primary=is_primary,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(assignment)
    session.flush()
    return assignment


def list_assignments_for_representative(
    session: Session,
    representative_id: uuid.UUID,
) -> Iterable[WarehouseAssignment]:
    """Return all warehouse assignments for a representative."""
    return session.execute(
        select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == representative_id,
        ).order_by(WarehouseAssignment.is_primary.desc(), WarehouseAssignment.effective_from.desc())
    ).scalars().all()


def list_assignments_for_warehouse(
    session: Session,
    warehouse_id: uuid.UUID,
) -> Iterable[WarehouseAssignment]:
    """Return all representative assignments for a warehouse."""
    return session.execute(
        select(WarehouseAssignment).where(
            WarehouseAssignment.warehouse_id == warehouse_id,
        ).order_by(WarehouseAssignment.effective_from.desc())
    ).scalars().all()


def delete_assignment(
    session: Session,
    *,
    representative_id: uuid.UUID,
    warehouse_id: uuid.UUID,
) -> None:
    """Remove a warehouse assignment.

    Raises: AssignmentNotFoundError.
    """
    assignment = session.execute(
        select(WarehouseAssignment).where(
            WarehouseAssignment.representative_id == representative_id,
            WarehouseAssignment.warehouse_id == warehouse_id,
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise AssignmentNotFoundError(representative_id, warehouse_id)
    session.delete(assignment)
    session.flush()


def delete_warehouse(session: Session, warehouse_id: uuid.UUID) -> None:
    """Hard-delete a warehouse if it is not referenced by any other records.

    Raises:
        WarehouseNotFoundError: if no non-deleted warehouse with this ID exists.
        WarehouseInUseError: if the warehouse is referenced by other records.
    """
    from sqlalchemy import func as sqlfunc

    wh = _get_warehouse_or_raise(session, warehouse_id)

    # Check FK references from transactional / catalog tables.
    from database.models.order import Order
    from database.models.order_line import OrderLine
    from database.models.inventory_transaction import InventoryTransaction
    from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot
    from database.models.warehouse_assignment import WarehouseAssignment
    from database.models.kpi_snapshot import KpiSnapshot
    from database.models.shipment import Shipment
    from database.models.stock_adjustment import StockAdjustment
    from database.models.stock_reservation import StockReservation
    from database.models.physical_count import PhysicalCount
    from database.models.stock_transfer import StockTransfer
    from database.models.warehouse_location import WarehouseLocation

    checks = [
        (Order, "fulfillment_warehouse_id", "orders"),
        (OrderLine, "fulfillment_warehouse_id", "order lines"),
        (InventoryTransaction, "warehouse_id", "inventory transactions"),
        (InventoryBalanceSnapshot, "warehouse_id", "inventory snapshots"),
        (WarehouseAssignment, "warehouse_id", "warehouse assignments"),
        (KpiSnapshot, "warehouse_id", "KPI snapshots"),
        (Shipment, "source_warehouse_id", "shipments"),
        (StockAdjustment, "warehouse_id", "stock adjustments"),
        (StockReservation, "warehouse_id", "stock reservations"),
        (PhysicalCount, "warehouse_id", "physical counts"),
        (StockTransfer, "source_warehouse_id", "stock transfers (source)"),
        (StockTransfer, "destination_warehouse_id", "stock transfers (destination)"),
        (WarehouseLocation, "warehouse_id", "warehouse locations"),
    ]

    refs = []
    for model, col, label in checks:
        count = session.execute(
            select(sqlfunc.count()).select_from(model).where(
                getattr(model, col) == warehouse_id
            )
        ).scalar_one()
        if count > 0:
            refs.append(f"{count} {label}")

    if refs:
        raise WarehouseInUseError(f"Cannot delete: still referenced by {', '.join(refs)}")

    session.delete(wh)
    session.flush()


__all__ = [
    "AssignmentNotFoundError",
    "DuplicateAssignmentError",
    "DuplicateWarehouseCodeError",
    "WarehouseNotFoundError",
    "WarehouseNotDeactivatableError",
    "WarehouseInUseError",
    "create_assignment",
    "create_warehouse",
    "deactivate_warehouse",
    "delete_assignment",
    "delete_warehouse",
    "get_warehouse",
    "list_assignments_for_representative",
    "list_assignments_for_warehouse",
    "list_warehouses",
    "update_warehouse",
]
