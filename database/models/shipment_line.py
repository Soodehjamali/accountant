"""``T15 — shipment_line`` ORM model (lines in a shipment matching order lines).

Authority: ``07_DATABASE_SPEC.md`` §T15 — ``T15 — shipment_line`` **has** a
full detailed section in the physical spec, so the spec is primary authority
here; ``06_ERD.md`` (F.5 — Fulfillment / Shipping, T15 line) is
secondary/corroborating only::

    T15 — shipment_line
    Purpose: Lines in a shipment matching order lines.
    PK: id (UUID)
    FK: shipment_id -> shipment.id; order_line_id -> order_line.id;
        product_id -> product.id; lot_id -> product_lot.id (nullable)
    Column Definitions: +UAC; shipment_id UUID NOT NULL; order_line_id UUID
        NOT NULL; product_id UUID NOT NULL; lot_id UUID NULL; qty_shipped
        NUMERIC(18,4) NOT NULL; unit_cost_at_ship NUMERIC(18,6) NOT NULL
        (snapshot for profit-margin calculation)
    Unique: uq_shipment_line (shipment_id, order_line_id)
    Check: ck_shipment_line_qty_positive (qty_shipped > 0)
    Business constraints: Sum(qty_shipped across all shipments for an
        order_line) must not exceed order_line.qty_ordered -- validated at
        the application layer when creating a shipment_line, then reflected
        back into order_line.qty_shipped.
    Recommended Indexes: btree on order_line_id; btree on product_id
    Composite Indexes: none beyond unique constraint
    Partial Indexes: none
    Partitioning Strategy: None directly; follows shipment's partitioning if
        adopted.
    Soft Delete Strategy: Supported
    Audit Strategy: Standard UAC

Owned by the Shipment aggregate (``06_ERD.md``: *"Shipment (root: shipment)
-- owns shipment_line, shipment_status_history"*) via ``shipment_id``, the
same aggregate ``shipment_status_history.py`` (T16) belongs to.

Non-reserved-word FK targets:
    ``shipment`` / ``order_line`` / ``product`` / ``product_lot`` are all
    ordinary identifiers -- no quoting concerns for any of the four FKs.

CRITICAL naming trap -- the unique constraint:
    The spec's literal constraint name is ``uq_shipment_line`` -- **not**
    ``uq_shipment_line_shipment_id_order_line_id``, which is what running
    the two columns through ``composite_descriptor`` and then
    ``uq_index_name`` would produce for an ordinary 2-column composite. This
    model instead calls ``UniqueConstraint("shipment_id", "order_line_id",
    name="uq_shipment_line")`` with the bare literal string -- the exact
    same treatment ``transfer_line.py`` already gave its own
    ``uq_transfer_line`` naming trap (and ``order_line.py`` its own
    ``uq_order_line``). Flagged explicitly so a future edit doesn't "clean
    this up" by routing it through
    ``composite_descriptor``/``uq_index_name`` and silently lengthening the
    name.

``lot_id`` nullable, same shape as ``order_line.lot_id`` /
``transfer_line.lot_id``:
    Targets ``product_lot.id`` and is nullable -- not every shipped product
    is lot-tracked. Declared with the same explicit
    ``_SAUuid(as_uuid=True)`` + ``ForeignKey(...)`` shape every other
    nullable-lot-FK column in this codebase already uses.

``unit_cost_at_ship`` -- ``cost_type()``, exact spec match:
    ``NUMERIC(18, 6)`` per spec -- ``database.types.cost_type()``'s own
    docstring names ``shipment_line.unit_cost_at_ship`` explicitly as one of
    its intended consumers (alongside ``inventory_transaction.unit_cost`` /
    ``transfer_line.unit_cost``), so this is a direct, documented fit rather
    than a placeholder choice. Spec note: *"Snapshot for profit-margin
    calculation"* -- application-layer semantics, not encoded here.

Column-type choices:

* ``qty_shipped`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, exact spec
  match. No spec default (the application always supplies it at
  shipment-line creation time, mirroring ``order_line.qty_ordered`` /
  ``transfer_line.qty_requested``'s own no-default treatment).
* ``unit_cost_at_ship`` -- ``cost_type()`` -> ``NUMERIC(18, 6)``, exact spec
  match (see dedicated note above).

Naming convention:
    The unique constraint is the naming-trap case explained above -- the
    bare literal ``name="uq_shipment_line"``, NOT
    ``uq_index_name(table, composite_descriptor(...))``. The CHECK uses
    ``ck_index_name`` normally: the standard helper output already matches
    the spec's literal name verbatim (``ck_shipment_line_qty_positive``) --
    no override needed. Every FK uses ``fk_index_name`` normally. Both
    recommended single-column indexes (``order_line_id``, ``product_id``)
    use ``idx_index_name`` with no override needed.

Out of scope for this model (not implemented here):
    * The "Sum(qty_shipped) must not exceed order_line.qty_ordered" business
      constraint, and reflecting the total back into
      ``order_line.qty_shipped`` -- the spec explicitly calls this
      application-layer validation (analogous to ``transfer_line``'s own
      "cannot receive more than dispatched" cross-row constraint), not a
      schema-level concern.
    * Partitioning -- spec marks this "None directly; follows shipment's
      partitioning if adopted", i.e. no partitioning is declared here at
      all.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional line item), spec §13:
    *"Standard UAC"*. ``ShipmentLine`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every other
    UAC-using line-item table (``order_line``, ``transfer_line``).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import cost_type, money_type


class ShipmentLine(Base, UniversalAuditColumns):
    """``T15 — shipment_line`` — lines in a shipment matching order lines (Classification: T)."""

    __tablename__ = "shipment_line"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- shipment_id
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "shipment.id",
            name=fk_index_name("shipment_line", "shipment_id", "shipment"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------- order_line_id
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order_line.id",
            name=fk_index_name("shipment_line", "order_line_id", "order_line"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("shipment_line", "product_id", "product"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("shipment_line", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- qty_shipped
    # No spec default -- application always supplies it (mirrors
    # order_line.qty_ordered / transfer_line.qty_requested's own
    # no-default treatment).
    qty_shipped: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------- unit_cost_at_ship
    # Exact spec match -- see module docstring's dedicated section.
    unit_cost_at_ship: Mapped[decimal.Decimal] = mapped_column(
        cost_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- deleted_at
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Bare literal name, NOT composite_descriptor + uq_index_name.
        UniqueConstraint(
            "shipment_id",
            "order_line_id",
            name="uq_shipment_line",
        ),
        # CHECK: qty_shipped strictly positive.
        CheckConstraint(
            "qty_shipped > 0",
            name=ck_index_name("shipment_line", "qty_positive"),
        ),
        # Recommended single-column indexes.
        Index(
            idx_index_name("shipment_line", "order_line_id"),
            "order_line_id",
        ),
        Index(
            idx_index_name("shipment_line", "product_id"),
            "product_id",
        ),
    )


__all__ = ["ShipmentLine"]
