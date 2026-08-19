"""``M5 — warehouse_location`` ORM model (optional bins/sub-locations within a warehouse).

Authority: ``06_ERD.md``, line 27 → ``M5 — warehouse_location``::

    M5 — warehouse_location (optional bins)
    Purpose: Sub-locations within a warehouse.
    PK: id
    FK: warehouse_id → warehouse
    Important fields: code, capacity
    Unique: (warehouse_id, code)
    Classification: M + soft-deletable

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``discount.py`` (H3), ``price_list.py``
(C3), etc.): ``06_ERD.md`` is ``warehouse_location``'s sole authority --
``warehouse_location`` has no detailed section in ``07_DATABASE_SPEC.md``
(confirmed by search: no ``07_DATABASE_SPEC.md`` mentions of
"warehouse_location" at all -- not even a cross-reference from another
table's FK note, unlike ``price_history`` / ``discount``, which were at
least mentioned as deferred-FK targets before their own sections existed).

FK is real from the outset:
    ``warehouse`` already exists in this codebase, so ``warehouse_id`` is
    declared as a real ``ForeignKey()`` from the start -- no deferred-FK
    section to write for this table. Not marked nullable in the ERD's ``FK:``
    line, so declared ``NOT NULL`` -- a sub-location with no parent
    warehouse is meaningless.

``code`` -- reuses ``warehouse.code``'s own factory, but NOT its
column-level ``unique=True``:
    ``code_short_type()`` (``VARCHAR(40)``) -- the same factory
    ``warehouse.code`` already uses for a short business-facing identifier.
    Unlike ``warehouse.code`` (which IS globally unique across the whole
    ``warehouse`` table, via column-level ``unique=True`` ->
    ``uq_warehouse_code``), this table's ``code`` is only unique *within
    its parent warehouse* -- the ERD's own ``Unique: (warehouse_id, code)``
    line makes this explicit (a composite constraint, not a bare
    column-level one). Declaring ``unique=True`` directly on this column
    would incorrectly force bin codes to be globally unique across every
    warehouse in the system, when the ERD only requires uniqueness scoped
    to one warehouse (e.g. both warehouse A and warehouse B can each have
    their own bin ``"A1"``). Uniqueness is therefore expressed only via the
    table-level ``UniqueConstraint`` below, not a column-level flag.
    ``NOT NULL`` -- no nullable annotation given, and a sub-location with no
    identifying code would be unusable/unselectable, the same reasoning
    already applied to ``price_list.name``.

``capacity`` -- reuses this codebase's general-purpose "precise decimal
quantity" convention, not a currency-specific one:
    ``money_type()`` (``NUMERIC(18, 4)``) -- despite the factory's name,
    this is already this codebase's established type for *any* precise
    decimal quantity, not only money: ``order_line.qty_ordered`` /
    ``stock_reservation.reserved_quantity`` /
    ``inventory_balance_snapshot.quantity_on_hand`` all use this exact same
    factory for physical unit counts, not currency amounts. ``capacity`` --
    how many units this bin/sub-location can physically hold -- is the same
    kind of value, so the same factory is reused here rather than
    introducing a new one. ``NOT NULL`` -- no nullable annotation given, and
    unlike ``discount``'s scope FKs or ``price_history``'s promo window
    (where nullability was overridden by clear logical necessity), there is
    no comparable "sometimes this concept doesn't apply at all" case for a
    defined location's own capacity, so the default "no annotation -> NOT
    NULL" reading is used as-is, with no override.

Unique constraint -- literal ERD column list, ordinary composite case (NOT
a naming trap):
    ``UniqueConstraint("warehouse_id", "code")`` via ``uq_index_name`` +
    ``composite_descriptor`` -- the ERD gives this constraint's columns
    explicitly (``Unique: (warehouse_id, code)``), so the standard helper
    output is used as-is with no override, the same ordinary treatment
    ``commission_config`` / ``price_history``'s own literal composite
    uniqueness already received (contrast ``order_line`` /
    ``stock_reservation``'s bare-literal-name naming traps, or
    ``discount``'s deliberately-absent constraint -- this ERD entry gives
    neither of those complications, just a plain literal column pair).

No CHECK given in the ERD beyond an obvious non-negativity bound on
``capacity``:
    The ERD names no vocabulary/enum field on this table at all (``code``
    is free text, ``capacity`` is a plain quantity) -- so there is no
    vocabulary CHECK to write here, unlike ``discount``/``price_list``/
    ``order_status_history``. One CHECK is still added,
    ``ck_warehouse_location_capacity_nonneg`` (``capacity >= 0``),
    following the same "add an obvious non-negativity bound on a physical
    quantity column even without an explicit spec instruction" precedent
    already established for ``order_line`` (``qty_*`` / ``unit_price``),
    ``stock_reservation.reserved_quantity``, and
    ``inventory_balance_snapshot``'s quantity columns.

Soft delete -- direct ``deleted_at``, same pattern as
``warehouse.py``/``product.py``:
    Per the ERD's own ``"M + soft-deletable"`` classification (the exact
    same classification tag ``warehouse.py`` itself carries), a nullable,
    timezone-aware ``TIMESTAMPTZ`` ``deleted_at`` column is declared
    directly -- ``NULL`` meaning not deleted -- with no soft-delete-query
    helper/mixin to lean on (this codebase has none; every soft-deletable
    table declares its own plain ``deleted_at`` column and relies on
    service-layer query filtering, per ``warehouse.py``'s own docstring
    note on this exact point).

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Plain ``M`` (master data) classification with the soft-delete
    qualifier -- an ordinary mutable master record, the same reasoning
    already established for ``warehouse.py`` / ``product.py`` (both ``M +
    soft-deletable``, both UAC). ``WarehouseLocation`` uses UAC and opts
    its ``version`` column into SQLAlchemy optimistic locking
    (``__mapper_args__ = {"version_id_col": "version"}``), consistent with
    every other UAC-using model in this codebase.

Naming convention:
    ``warehouse_id`` uses ``fk_index_name`` normally ->
    ``fk_warehouse_location_warehouse_id_warehouse_id``. The unique
    constraint uses ``uq_index_name`` + ``composite_descriptor`` as an
    ordinary composite case -> ``uq_warehouse_location_warehouse_id_code``.
    The CHECK uses ``ck_index_name`` normally ->
    ``ck_warehouse_location_capacity_nonneg``.

Column-type choices:

* ``code`` -- ``code_short_type()`` -> ``VARCHAR(40)`` (no column-level
  ``unique=True`` -- see dedicated note above).
* ``capacity`` -- ``money_type()`` -> ``NUMERIC(18, 4)`` (see dedicated
  note above).
* ``deleted_at`` -- ``DateTime(timezone=True)``, nullable, no default.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import code_short_type, money_type


class WarehouseLocation(Base, UniversalAuditColumns):
    """``M5 — warehouse_location`` — sub-locations (optional bins) within a warehouse (Classification: M + soft-deletable)."""

    __tablename__ = "warehouse_location"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("warehouse_location", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------------- code
    # No column-level unique=True -- uniqueness is scoped to (warehouse_id,
    # code), not global. See module docstring's dedicated section.
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------ capacity
    capacity: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker (same pattern as warehouse.py /
    # product.py); NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- ordinary composite case, literal ERD column pair.
        UniqueConstraint(
            "warehouse_id",
            "code",
            name=uq_index_name(
                "warehouse_location",
                composite_descriptor(("warehouse_id", "code")),
            ),
        ),
        # CHECK: capacity non-negative -- no ERD-given vocabulary CHECK on
        # this table, see module docstring's dedicated section.
        CheckConstraint(
            "capacity >= 0",
            name=ck_index_name("warehouse_location", "capacity_nonneg"),
        ),
    )


__all__ = ["WarehouseLocation"]
