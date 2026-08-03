"""``T11 — order_line`` ORM model (order lines, frozen resolved price/discount).

Authority: ``07_DATABASE_SPEC.md`` §T11 — ``T11 — order_line`` **has** a full
detailed section in the physical spec (same as ``order`` / T10), so the spec
is primary authority here; ``06_ERD.md`` (F.4 — Sales / Order, T11 line) is
secondary/corroborating only::

    T11 — order_line
    Purpose: Order lines with frozen resolved price/discount, immutable
        after approval.
    PK: id (UUID)
    FK: order_id -> order.id; product_id -> product.id;
        lot_id -> product_lot.id (nullable);
        fulfillment_warehouse_id -> warehouse.id;
        discount_id -> discount.id (nullable);
        price_history_id -> price_history.id
    Unique: uq_order_line (order_id, product_id, lot_id)
    Check: ck_order_line_qty_nonneg (qty_ordered >= 0 AND qty_reserved >= 0
        AND qty_shipped >= 0 AND qty_returned >= 0);
        ck_order_line_shipped_le_ordered (qty_shipped <= qty_ordered);
        ck_order_line_unit_price_nonneg (unit_price >= 0)
    Business constraints: unit_price/discount_value/price_history_id become
        immutable once the parent order.state passes APPROVED -- enforced
        via a BEFORE UPDATE trigger checking the parent order's current
        state (a cross-table check, not expressible as a CHECK constraint).
    Soft Delete Strategy: Supported pre-approval only.
    Audit Strategy: Standard UAC.
    Notes: --

Retrofitted ForeignKey() -- ``discount_id`` AND ``price_history_id``:
    ``discount`` (H3) now exists, so ``discount_id`` carries a real
    ``ForeignKey("discount.id")``, nullable (spec: *"FK, snapshot
    reference"*), named via ``fk_index_name`` as usual.

    ``price_history`` (H1) also now exists, so ``price_history_id`` -->
    ``price_history.id`` is retrofitted in this same change into a real
    ``ForeignKey("price_history.id")`` too. Spec: *"FK, frozen price
    provenance"* -- unlike ``discount_id``, the spec gives **no**
    nullability qualifier here, so this column stays ``nullable=False``,
    now paired with a real (not deferred) ``ForeignKey()``. This closes the
    **last** deferred-FK deviation remaining anywhere in this codebase --
    every column that was ever declared as a plain ``Uuid`` pending a
    not-yet-built target table (``app_user``, ``product_lot``,
    ``discount``, and now ``price_history``) has been retrofitted; a
    project-wide search for the deferred-FK deviation phrasing finds no
    remaining occurrence pointing at a target table that doesn't exist.

CRITICAL naming trap -- the unique constraint:
    The spec's literal constraint name is ``uq_order_line`` -- **not**
    ``uq_order_line_order_id_product_id_lot_id``, which is what running the
    three columns through ``composite_descriptor`` and then
    ``uq_index_name`` would produce for an ordinary 3-column composite. This
    model instead calls ``UniqueConstraint("order_id", "product_id",
    "lot_id", name="uq_order_line")`` with the bare literal string, the same
    treatment ``order.py`` already gave its own ``uq_order_number`` naming
    trap (see that module's docstring). Flagged explicitly so a future edit
    doesn't "clean this up" by routing it through
    ``composite_descriptor``/``uq_index_name`` and silently doubling the
    name.

Reserved-word FK target -- ``order_id -> order.id``:
    ``order`` is a reserved SQL keyword; ``order.py`` already resolved the
    physical-table-name question by choosing the literal ``__tablename__ =
    "order"`` (see that module's "Reserved-word table name" docstring
    section) specifically so that every downstream FK target -- this
    table's own ``order_id``, plus ``order_status_history.order_id`` -- can
    reference the literal string ``"order.id"`` without a mismatch.
    ``order_id`` here reuses that same literal string; SQLAlchemy's
    dialect-aware identifier preparer auto-quotes the reserved identifier
    for PostgreSQL wherever it's emitted, with no extra configuration
    needed on this model.

``fulfillment_warehouse_id`` nullability -- deliberately diverges from
``order.py``'s own column of the same name:
    ``order.fulfillment_warehouse_id`` is nullable ("set once reserved" per
    that table's spec). This table's ``fulfillment_warehouse_id`` is
    different: the spec's own Column Definitions row for T11 gives it
    ``NOT NULL`` with no qualifier -- a line-level fulfillment warehouse is
    mandatory at line-creation time, unlike the order-level "not yet
    assigned" case. This model does **not** copy ``order.py``'s
    nullability here; the two columns share a name but not a nullability
    contract.

Column-type choices:

* ``qty_ordered`` / ``qty_reserved`` / ``qty_shipped`` / ``qty_returned`` /
  ``unit_price`` / ``discount_value`` / ``line_total`` -- ``money_type()``
  -> ``NUMERIC(18, 4)``, an exact match to the spec's ``NUMERIC(18,4)`` for
  every one of these columns. ``qty_reserved`` / ``qty_shipped`` /
  ``qty_returned`` / ``discount_value`` additionally mirror ``order.py``'s
  own money-column pattern of a dual ``default=0`` / ``server_default=
  sa_text("0")`` declaration, matching the spec's ``DEFAULT 0``.
  ``qty_ordered`` / ``unit_price`` / ``line_total`` have no spec default
  (``unit_price``: "Frozen at approval"; ``line_total``: "Computed at write
  time by the application" -- the application always supplies it, so no
  column-level default is declared for either).
* ``fulfillment_mode`` -- ``state_token_long_type()`` -> ``VARCHAR(24)``,
  used as the closest existing factory for the spec's ``VARCHAR(20)`` --
  the same placeholder treatment ``order.py`` already gave its own
  ``sales_channel`` / ``fulfillment_mode`` columns (no exact 20-width
  factory exists in ``database.types``). Unlike ``order.fulfillment_mode``,
  this column carries **no** CHECK constraint: the spec's own T11 Check
  Constraints list (point 6) enumerates exactly three checks --
  ``ck_order_line_qty_nonneg`` / ``ck_order_line_shipped_le_ordered`` /
  ``ck_order_line_unit_price_nonneg`` -- and does not include a
  fulfillment_mode vocabulary check for this table. This column is
  documented as *"Snapshot of the order's mode at line-creation time"* --
  a copy of an already-validated value, not an independently-validated
  field in its own right -- so no CHECK is added here even though
  ``order.fulfillment_mode`` has one.

Naming convention:
    The unique constraint is the naming-trap case explained above --
    the bare literal ``name="uq_order_line"``, NOT
    ``uq_index_name(table, composite_descriptor(...))``. Every CHECK below
    uses ``ck_index_name`` normally: for this table the standard helper
    output already matches the spec's three literal names verbatim
    (``ck_order_line_qty_nonneg``, ``ck_order_line_shipped_le_ordered``,
    ``ck_order_line_unit_price_nonneg``) -- no override needed, verified by
    inspection of the assembled ``ck_<table>_<descriptor>`` convention
    template against each spec name. Every FK uses ``fk_index_name``
    normally (``order_id``, ``product_id``, ``lot_id``,
    ``fulfillment_warehouse_id``, ``discount_id``, and now
    ``price_history_id`` -- every FK column on this table is a real FK as
    of this change; there is no longer a deferred-FK column left on this
    model at all). The explicit
    ``order_id`` index and the recommended ``product_id`` index use
    ``idx_index_name`` with no override needed. The partial index
    ``idx_order_line_open`` is likewise produced by plain
    ``idx_index_name("order_line", "open")`` -- the helper's normal output
    already matches the spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * The ``BEFORE UPDATE`` immutability trigger guarding
      ``unit_price`` / ``discount_value`` / ``price_history_id`` once the
      parent ``order.state`` passes ``APPROVED`` -- the spec explicitly
      calls this a cross-table check, not expressible as a CHECK
      constraint (a migration/DDL-level concern, not a model-level one).
    * Range/foreign-table partitioning following ``order``'s partitioning
      key -- the spec marks this a physical-design decision to confirm
      with the DBA at migration time, not required now.
    * The "pre-approval only" qualifier on the Soft Delete Strategy -- this
      depends on the parent order's current state, so it is a service-layer
      rule, not a schema difference. ``deleted_at`` itself is
      unconditionally nullable at the column level; the restriction is
      documented on the column below rather than encoded in SQL.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import money_type, state_token_long_type


class OrderLine(Base, UniversalAuditColumns):
    """``T11 — order_line`` — order lines, frozen resolved price/discount, immutable after approval (Classification: T)."""

    __tablename__ = "order_line"

    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- order_id
    # Reserved-word FK target -- reuses order.py's own literal "order.id"
    # string. See module docstring's "Reserved-word FK target" section.
    order_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order.id",
            name=fk_index_name("order_line", "order_id", "order"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("order_line", "product_id", "product"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("order_line", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ------------------------------------------------- fulfillment_warehouse_id
    # NOT NULL -- diverges from order.py's own nullable column of the same
    # name. See module docstring's dedicated section on this.
    fulfillment_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("order_line", "fulfillment_warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- qty columns
    qty_ordered: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )
    qty_reserved: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    qty_shipped: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    qty_returned: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------- unit_price
    # "Frozen at approval" -- no default, application always supplies it.
    unit_price: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # --------------------------------------------------------- discount_value
    # "Frozen" -- mirrors order.py's own money-column default=0 pattern.
    discount_value: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------- discount_id
    # Real FK -- discount (H3) now exists. Nullable per spec: snapshot
    # reference.
    discount_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "discount.id",
            name=fk_index_name("order_line", "discount_id", "discount"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------- price_history_id
    # Real FK -- price_history (H1) now exists. NOT NULL per spec (no
    # nullability qualifier given, unlike discount_id). This is the last
    # deferred-FK retrofit remaining in the codebase -- see module
    # docstring's "Retrofitted ForeignKey()" section.
    price_history_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "price_history.id",
            name=fk_index_name("order_line", "price_history_id", "price_history"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- line_total
    # "Computed at write time by the application" -- no default at all;
    # the application always supplies this value.
    line_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ----------------------------------------------------------- fulfillment_mode
    # Placeholder width -- see module docstring's column-type-choices note.
    # Deliberately no CHECK constraint here (unlike order.fulfillment_mode)
    # -- this is a line-creation-time snapshot of the order's own
    # already-validated value, not an independently-validated field, and
    # the spec's own T11 Check Constraints list does not include one.
    fulfillment_mode: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- deleted_at
    # Unconditionally nullable at the column level. Spec: "Soft Delete
    # Strategy: Supported pre-approval only" -- the "pre-approval only"
    # restriction depends on the parent order's current state, so it is a
    # service-layer rule, not something expressible in this column's SQL
    # shape. See module docstring's "Out of scope" section.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Bare literal name, NOT composite_descriptor + uq_index_name.
        UniqueConstraint(
            "order_id",
            "product_id",
            "lot_id",
            name="uq_order_line",
        ),
        # CHECK: all four qty columns >= 0, as ONE combined constraint --
        # the spec gives this as a single constraint, mirroring order's own
        # totals_nonneg treatment.
        CheckConstraint(
            "qty_ordered >= 0 AND qty_reserved >= 0 AND qty_shipped >= 0 AND qty_returned >= 0",
            name=ck_index_name("order_line", "qty_nonneg"),
        ),
        # CHECK: shipped never exceeds ordered.
        CheckConstraint(
            "qty_shipped <= qty_ordered",
            name=ck_index_name("order_line", "shipped_le_ordered"),
        ),
        # CHECK: unit_price non-negative.
        CheckConstraint(
            "unit_price >= 0",
            name=ck_index_name("order_line", "unit_price_nonneg"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("order_line", "product_id"),
            "product_id",
        ),
        # Explicit btree on order_id -- spec calls this out explicitly
        # despite the FK/unique constraint giving implicit coverage
        # ("beyond what the unique constraint covers").
        Index(
            idx_index_name("order_line", "order_id"),
            "order_id",
        ),
        # Partial index -- open lines (not yet fully shipped) per
        # fulfillment warehouse / product.
        Index(
            idx_index_name("order_line", "open"),
            "fulfillment_warehouse_id",
            "product_id",
            postgresql_where=sa_text("qty_shipped < qty_ordered"),
        ),
    )


__all__ = ["OrderLine"]
