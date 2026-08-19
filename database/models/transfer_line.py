"""``T5 — transfer_line`` ORM model (line items of a stock transfer).

Authority: ``07_DATABASE_SPEC.md`` §T5 — ``T5 — transfer_line`` **has** a full
detailed section in the physical spec, so the spec is primary authority here;
``06_ERD.md`` (line 54, F.2 — Stock Movement) is secondary/corroborating
only::

    T5 — transfer_line
    Purpose: Line items of a transfer -- product/qty split across
        requested/dispatched/received, plus cost.
    PK: id (UUID)
    FK: stock_transfer_id -> stock_transfer.id; product_id -> product.id;
        lot_id -> product_lot.id (nullable)
    Column Definitions: +UAC; stock_transfer_id UUID NOT NULL; product_id
        UUID NOT NULL; lot_id UUID NULL; qty_requested NUMERIC(18,4) NOT
        NULL; qty_dispatched NUMERIC(18,4) NOT NULL DEFAULT 0; qty_received
        NUMERIC(18,4) NOT NULL DEFAULT 0; unit_cost NUMERIC(18,6) NOT NULL
        (seeds purchase_price_history for an OWNED destination); qty_variance
        NUMERIC(18,4) NOT NULL DEFAULT 0 (qty_dispatched - qty_received,
        stored, application-computed at receipt)
    Unique: uq_transfer_line (stock_transfer_id, product_id, lot_id)
    Check: ck_transfer_line_qty_nonneg (qty_requested >= 0 AND
        qty_dispatched >= 0 AND qty_received >= 0);
        ck_transfer_line_dispatched_le_requested (qty_dispatched <=
        qty_requested); ck_transfer_line_received_le_dispatched
        (qty_received <= qty_dispatched)
    Business constraints: Cannot receive more than dispatched (enforced by
        the CHECK above at the row level, but also validated at the
        application layer since receipt happens incrementally across
        multiple partial-receipt events, not in one UPDATE).
    Recommended Indexes: btree on product_id
    Composite Indexes: none beyond unique constraint
    Partial Indexes: idx_transfer_line_open ON transfer_line
        (stock_transfer_id) WHERE qty_received < qty_dispatched
    Soft Delete Strategy: Supported
    Audit Strategy: Standard UAC

Owned by the StockTransfer aggregate (``06_ERD.md``: *"StockTransfer (root:
stock_transfer) -- owns transfer_line, transfer_history"*) via
``stock_transfer_id``.

Reserved-word-adjacent FK target -- ``stock_transfer_id -> stock_transfer.id``:
    ``stock_transfer`` is an ordinary (non-reserved) identifier -- unlike
    ``order.py``'s own situation, no quoting concern applies here; the
    literal ``"stock_transfer.id"`` string is used with no special
    treatment.

CRITICAL naming trap -- the unique constraint:
    The spec's literal constraint name is ``uq_transfer_line`` -- **not**
    ``uq_transfer_line_stock_transfer_id_product_id_lot_id``, which is what
    running the three columns through ``composite_descriptor`` and then
    ``uq_index_name`` would produce for an ordinary 3-column composite. This
    model instead calls ``UniqueConstraint("stock_transfer_id", "product_id",
    "lot_id", name="uq_transfer_line")`` with the bare literal string -- the
    same treatment ``order_line.py`` already gave its own
    ``uq_order_line`` naming trap (see that module's docstring). Flagged
    explicitly so a future edit doesn't "clean this up" by routing it
    through ``composite_descriptor``/``uq_index_name`` and silently
    lengthening the name.

``lot_id`` nullable, same shape as ``order_line.lot_id``:
    Both target ``product_lot.id`` and are nullable -- not every
    transferred product is lot-tracked. Declared with the same explicit
    ``_SAUuid(as_uuid=True)`` + ``ForeignKey(...)`` shape every other
    nullable-lot-FK column in this codebase already uses.

``unit_cost`` -- ``cost_type()``, exact spec match:
    ``NUMERIC(18, 6)`` per spec -- ``database.types.cost_type()``'s own
    docstring names ``transfer_line.unit_cost`` explicitly as one of its
    intended consumers (alongside ``inventory_transaction.unit_cost`` /
    ``shipment_line.unit_cost_at_ship``), so this is a direct, documented
    fit rather than a placeholder choice. Spec note: *"Seeds
    purchase_price_history for an OWNED destination"* -- application-layer
    behaviour, not encoded here.

``qty_variance`` -- stored, application-computed, deliberately NO CHECK:
    Spec: *"qty_dispatched - qty_received, stored (application-computed at
    receipt)"* -- a derived value the application writes at receipt time,
    not a database-computed generated column (no such column exists
    elsewhere in this codebase) and not a value the spec's own §6 Check
    Constraints list (three named checks, none involving
    ``qty_variance``) independently bounds. ``NOT NULL DEFAULT 0`` --
    correct starting value before any receipt has occurred (`0 - 0 = 0`).

Column-type choices:

* ``qty_requested`` / ``qty_dispatched`` / ``qty_received`` /
  ``qty_variance`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, an exact match
  to the spec's ``NUMERIC(18,4)`` for all four. ``qty_dispatched`` /
  ``qty_received`` / ``qty_variance`` additionally mirror ``order.py`` /
  ``order_line.py``'s own money-column dual ``default=0`` /
  ``server_default=sa_text("0")`` pattern, matching the spec's ``DEFAULT
  0``. ``qty_requested`` has no spec default -- the application always
  supplies it at line-creation time (mirrors ``order_line.qty_ordered``'s
  own no-default treatment).
* ``unit_cost`` -- ``cost_type()`` -> ``NUMERIC(18, 6)``, exact spec match
  (see dedicated note above).

Naming convention:
    The unique constraint is the naming-trap case explained above -- the
    bare literal ``name="uq_transfer_line"``, NOT
    ``uq_index_name(table, composite_descriptor(...))``. Every CHECK below
    uses ``ck_index_name`` normally: the standard helper output already
    matches the spec's three literal names verbatim
    (``ck_transfer_line_qty_nonneg``,
    ``ck_transfer_line_dispatched_le_requested``,
    ``ck_transfer_line_received_le_dispatched``) -- no override needed.
    Every FK uses ``fk_index_name`` normally. The recommended ``product_id``
    index uses ``idx_index_name`` with no override needed. The partial index
    ``idx_transfer_line_open`` is likewise produced by plain
    ``idx_index_name("transfer_line", "open")`` -- the helper's normal
    output already matches the spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * The "cannot receive more than dispatched" business-layer validation
      across multiple partial-receipt events -- the spec explicitly notes
      the CHECK constraint alone is insufficient for the incremental-receipt
      case and application-layer validation is also required; that
      validation logic is a service-layer concern, not a schema one.
    * "Seeds purchase_price_history for an OWNED destination" -- an
      application-orchestrated side effect at receipt time, not a
      schema-level concern.
    * Range/foreign-table partitioning following the parent's low-to-
      moderate volume -- spec marks this "None (inherits parent's
      low-to-moderate volume)", i.e. no partitioning is needed at all.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional line item), spec §13:
    *"Standard UAC"*. ``TransferLine`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every
    other UAC-using line-item table (``order_line``).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import cost_type, money_type


class TransferLine(Base, UniversalAuditColumns):
    """``T5 — transfer_line`` — line items of a stock transfer (Classification: T)."""

    __tablename__ = "transfer_line"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ---------------------------------------------------- stock_transfer_id
    stock_transfer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "stock_transfer.id",
            name=fk_index_name("transfer_line", "stock_transfer_id", "stock_transfer"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("transfer_line", "product_id", "product"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("transfer_line", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- qty_requested
    # No spec default -- application always supplies it (mirrors
    # order_line.qty_ordered's own no-default treatment).
    qty_requested: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ------------------------------------------------------------ qty_dispatched
    qty_dispatched: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # -------------------------------------------------------------- qty_received
    qty_received: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ----------------------------------------------------------------- unit_cost
    # Exact spec match -- see module docstring's dedicated section.
    unit_cost: Mapped[decimal.Decimal] = mapped_column(
        cost_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- qty_variance
    # Stored, application-computed at receipt. See module docstring's
    # dedicated section.
    qty_variance: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
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
            "stock_transfer_id",
            "product_id",
            "lot_id",
            name="uq_transfer_line",
        ),
        # CHECK: all three qty columns >= 0, as ONE combined constraint --
        # the spec gives this as a single constraint, mirroring order_line's
        # own qty_nonneg treatment.
        CheckConstraint(
            "qty_requested >= 0 AND qty_dispatched >= 0 AND qty_received >= 0",
            name=ck_index_name("transfer_line", "qty_nonneg"),
        ),
        # CHECK: dispatched never exceeds requested.
        CheckConstraint(
            "qty_dispatched <= qty_requested",
            name=ck_index_name("transfer_line", "dispatched_le_requested"),
        ),
        # CHECK: received never exceeds dispatched.
        CheckConstraint(
            "qty_received <= qty_dispatched",
            name=ck_index_name("transfer_line", "received_le_dispatched"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("transfer_line", "product_id"),
            "product_id",
        ),
        # Partial index -- open lines (not yet fully received) per transfer.
        Index(
            idx_index_name("transfer_line", "open"),
            "stock_transfer_id",
            postgresql_where=sa_text("qty_received < qty_dispatched"),
        ),
    )


__all__ = ["TransferLine"]
