"""Representative Data Scope authorization service.

Per ADR-007, this module is the **single shared entry point** for all
consumers that need to resolve which Customers or Warehouses a
Representative is authorized to access.  Bot commands, API endpoints,
reporting, and the future representative portal all call the same
functions.

**Zero platform knowledge.**  This module imports nothing from
``telegram_adapter/`` or any adapter layer.

Scope resolution reads through the existing assignment tables:

* ``customer_rep_assignment`` (C6) for Representative → Customer.
* ``warehouse_assignment`` (C5) for Representative → Warehouse.

No new tables, constraints, triggers, or columns are added for scoping
purposes.

Design:
    ``resolve_representative_customers()``
        Returns the list of Customer rows assigned to a given
        Representative, filtered by the time window
        (``effective_from`` / ``effective_to``) at a point in time.
        Ordered by ``priority`` (ascending = highest priority first),
        then ``effective_from`` (descending = most recent first).

    ``resolve_representative_warehouses()``
        Returns the list of Warehouse rows assigned to a given
        Representative, filtered by the time window at a point in time.
        When ``primary_only=True``, returns only the warehouse where
        ``is_primary=True``.  Ordered by ``is_primary`` (descending =
        primary first), then ``effective_from`` (descending).

Authorization:
    Every function requires a ``representative_id`` argument.  The
    caller is responsible for ensuring this ID matches the identity of
    the requesting user (e.g. the bot session's ``representative_id``).
    This module does NOT perform identity resolution -- it only
    enforces that results are scoped to the given representative.

Per ADR-007 §5:
    Scope enforcement lives here, in the service layer, NOT in any
    platform adapter or command handler.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RepresentativeNotFoundError(LookupError):
    """Raised when a ``representative_id`` has no matching row."""

    def __init__(self, representative_id: uuid.UUID) -> None:
        super().__init__(
            f"No representative with id '{representative_id}' exists."
        )
        self.representative_id = representative_id


# ---------------------------------------------------------------------------
# Scope resolution: Representative → Customer
# ---------------------------------------------------------------------------


def resolve_representative_customers(
    session: Session,
    representative_id: uuid.UUID,
    *,
    at: datetime.datetime | None = None,
) -> list[Customer]:
    """Return the Customers currently assigned to ``representative_id``.

    A customer assignment is "active" when::

        effective_from <= at
        AND (effective_to IS NULL OR effective_to > at)

    where ``at`` defaults to ``datetime.now(timezone.utc)``.

    Results are ordered by:
        1. ``priority`` ascending (lowest number = highest priority)
        2. ``effective_from`` descending (most recent assignment first)

    If the representative does not exist, ``RepresentativeNotFoundError``
    is raised.

    Per ADR-007 §1 and §4:
        Only customers belonging to this representative are returned.
        No cross-representative data is included.
    """
    if at is None:
        at = datetime.datetime.now(datetime.timezone.utc)

    # Validate representative exists.
    from database.models.representative import Representative
    rep = session.get(Representative, representative_id)
    if rep is None:
        raise RepresentativeNotFoundError(representative_id)

    # Query active assignments for this representative, joined to Customer.
    stmt = (
        select(Customer)
        .join(
            CustomerRepAssignment,
            CustomerRepAssignment.customer_id == Customer.id,
        )
        .where(
            CustomerRepAssignment.representative_id == representative_id,
            CustomerRepAssignment.effective_from <= at,
            (
                CustomerRepAssignment.effective_to.is_(None)
                | (CustomerRepAssignment.effective_to > at)
            ),
            Customer.deleted_at.is_(None),
        )
        .order_by(
            CustomerRepAssignment.priority.asc(),
            CustomerRepAssignment.effective_from.desc(),
        )
    )

    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Scope resolution: Representative → Warehouse
# ---------------------------------------------------------------------------


def resolve_representative_warehouses(
    session: Session,
    representative_id: uuid.UUID,
    *,
    at: datetime.datetime | None = None,
    primary_only: bool = False,
) -> list[Warehouse]:
    """Return Warehouses assigned to ``representative_id``.

    A warehouse assignment is "active" when::

        effective_from <= at
        AND (effective_to IS NULL OR effective_to > at)

    where ``at`` defaults to ``datetime.now(timezone.utc)``.

    When ``primary_only=True``, only the warehouse where
    ``is_primary=True`` is returned (if it is currently active).
    If no primary warehouse is active, returns an empty list.

    Results are ordered by:
        1. ``is_primary`` descending (primary first)
        2. ``effective_from`` descending (most recent assignment first)

    If the representative does not exist,
    ``RepresentativeNotFoundError`` is raised.

    Per ADR-007 §2 and §4:
        Only warehouses belonging to this representative are returned.
        No cross-representative data is included.
    """
    if at is None:
        at = datetime.datetime.now(datetime.timezone.utc)

    # Validate representative exists.
    from database.models.representative import Representative
    rep = session.get(Representative, representative_id)
    if rep is None:
        raise RepresentativeNotFoundError(representative_id)

    stmt = (
        select(Warehouse)
        .join(
            WarehouseAssignment,
            WarehouseAssignment.warehouse_id == Warehouse.id,
        )
        .where(
            WarehouseAssignment.representative_id == representative_id,
            WarehouseAssignment.effective_from <= at,
            (
                WarehouseAssignment.effective_to.is_(None)
                | (WarehouseAssignment.effective_to > at)
            ),
            Warehouse.deleted_at.is_(None),
        )
    )

    if primary_only:
        stmt = stmt.where(WarehouseAssignment.is_primary.is_(True))

    stmt = stmt.order_by(
        WarehouseAssignment.is_primary.desc(),
        WarehouseAssignment.effective_from.desc(),
    )

    return list(session.execute(stmt).scalars().all())


__all__ = [
    "RepresentativeNotFoundError",
    "resolve_representative_customers",
    "resolve_representative_warehouses",
]
