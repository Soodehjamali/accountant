"""``T9 — physical_count_line`` ORM model (counted vs. expected quantity per product/lot).

Authority: ``07_DATABASE_SPEC.md`` §T9 — ``T9 — physical_count_line``
**has** a full detailed section in the physical spec, so the spec is
primary authority here; ``06_ERD.md`` (line 59, F.3 — Physical Count) is
secondary/corroborating only::

    T9 — physical_count_line
    Purpose: Counted vs. expected quantity per product/lot within a count
        session, with computed delta.
    PK: id (UUID)
    FK: physical_count_id -> physical_count.id; product_id -> product.id;
        lot_id -> product_lot.id (nullable); reason_code_id ->
        reason_code_ref.id (nullable)
    Column Definitions: +UAC; physical_count_id UUID NOT NULL; product_id
        UUID NOT NULL; lot_id UUID NULL; reason_code_id UUID NULL (required
        if delta <> 0 at reconciliation); expected_qty NUMERIC(18,4) NOT
        NULL (snapshot from inventory_balance_snapshot at count-open time);
        counted_qty NUMERIC(18,4) NULL (physically counted quantity); delta
        NUMERIC(18,4) NOT NULL DEFAULT 0 (counted_qty - expected_qty,
        application-computed on entry); reconciled BOOLEAN NOT NULL DEFAULT
        false (true once a corresponding stock_adjustment has been posted)
    Unique: uq_physical_count_line (physical_count_id, product_id, lot_id)
    Check: ck_physical_count_line_expected_nonneg (expected_qty >= 0)
    Business constraints: A non-zero delta requires reason_code_id before
        the parent count can transition to RECONCILED
        (application-enforced).
    Recommended Indexes: btree on product_id
    Composite Indexes: none beyond unique constraint
    Partial Indexes: idx_physical_count_line_unreconciled ON
        physical_count_line (physical_count_id) WHERE reconciled = false
        AND delta <> 0
    Soft Delete Strategy: Supported before parent count closes
    Audit Strategy: Standard UAC

Owned by the PhysicalCount aggregate (``06_ERD.md``: *"PhysicalCount (root:
physical_count) -- owns physical_count_line"*) via ``physical_count_id``,
the parent header table (T8) built in this same change.

CRITICAL naming trap -- the unique constraint:
    The spec's literal constraint name is ``uq_physical_count_line`` --
    **not** ``uq_physical_count_line_physical_count_id_product_id_lot_id``,
    which is what running the three columns through
    ``composite_descriptor`` and then ``uq_index_name`` would produce for
    an ordinary 3-column composite. This model instead calls
    ``UniqueConstraint("physical_count_id", "product_id", "lot_id",
    name="uq_physical_count_line")`` with the bare literal string -- the
    same treatment ``order_line.py`` (``uq_order_line``) and
    ``transfer_line.py`` (``uq_transfer_line``) already gave their own
    identically-shaped naming traps. Flagged explicitly so a future edit
    doesn't "clean this up" by routing it through
    ``composite_descriptor``/``uq_index_name`` and silently lengthening the
    name.

``lot_id`` nullable, same shape as ``order_line.lot_id`` /
``transfer_line.lot_id``:
    Targets ``product_lot.id``, nullable -- not every counted product is
    lot-tracked. Declared with the same explicit
    ``_SAUuid(as_uuid=True)`` + ``ForeignKey(...)`` shape every other
    nullable-lot-FK column in this codebase already uses.

``reason_code_id`` -- nullable, unlike ``stock_adjustment.reason_code_id``:
    ``stock_adjustment.reason_code_id`` (T7, this same change) is
    ``NOT NULL`` -- every adjustment has an unconditional reason. This
    column is different: the spec's own §3/§4 explicitly marks it
    ``NULL`` -- *"required if delta <> 0 at reconciliation"* -- a
    conditional requirement enforced at the application layer when the
    parent count transitions to ``RECONCILED`` (spec §7), not an
    unconditional one. This model does not copy
    ``stock_adjustment.reason_code_id``'s nullability here; the two
    columns share a target table but not a nullability contract, matching
    the exact "shared name, different nullability contract" precedent
    ``order_line.fulfillment_warehouse_id`` already documents relative to
    ``order.fulfillment_warehouse_id``.

``expected_qty`` / ``counted_qty`` -- opposite nullability, both undefaulted:
    ``expected_qty`` is ``NOT NULL`` -- *"Snapshot from
    inventory_balance_snapshot at count-open time"* -- always known the
    instant the line is created. ``counted_qty`` is ``NULL`` -- *"Physically
    counted quantity"* -- genuinely unknown until someone performs the
    physical count, which happens after the line row already exists (the
    session moves ``OPEN`` -> ``COUNTING`` -> ...). Neither carries a
    column-level default; both are values the application always supplies
    explicitly (at line-creation time for ``expected_qty``, at count-entry
    time for ``counted_qty``).

``delta`` -- stored, application-computed, deliberately NO CHECK beyond
``expected_nonneg``:
    Spec: *"counted_qty - expected_qty, application-computed on entry"* --
    the same "derived value the application writes, not a database-computed
    generated column" treatment ``transfer_line.qty_variance`` already
    receives. ``NOT NULL DEFAULT 0`` -- correct starting value before any
    count entry (`NULL - expected_qty` would itself be `NULL`, so the
    application writes `0` as the placeholder until ``counted_qty`` is
    entered). The spec's own §6 Check Constraints list names exactly one
    constraint for this table (``ck_physical_count_line_expected_nonneg``,
    on ``expected_qty`` only) -- ``delta`` itself carries no CHECK, since
    it is legitimately signed (a shortage is negative, an overage is
    positive).

``reconciled`` -- ``Boolean()``, same shape as
``stock_adjustment.threshold_marker``:
    Mirrors ``currency.is_base`` / ``stock_adjustment.threshold_marker``'s
    own ``Boolean()`` + dual ``default=False`` /
    ``server_default=sa_text("false")`` declaration shape exactly. No
    partial-uniqueness index is attached to it (unlike ``currency.is_base``)
    -- it is a plain per-row completion flag, *"true once a corresponding
    stock_adjustment has been posted"* (an application-orchestrated side
    effect, not a database-enforced invariant), matching the spec's own
    §5/§6 silence on any constraint involving it.

Column-type choices:

* ``expected_qty`` / ``counted_qty`` / ``delta`` -- ``money_type()`` ->
  ``NUMERIC(18, 4)``, an exact match to the spec's ``NUMERIC(18,4)`` for
  all three.
* ``reconciled`` -- ``sqlalchemy.Boolean()``, ``NOT NULL DEFAULT false``
  (see dedicated note above).

Naming convention:
    The unique constraint is the naming-trap case explained above -- the
    bare literal ``name="uq_physical_count_line"``, NOT
    ``uq_index_name(table, composite_descriptor(...))``. The single CHECK
    uses ``ck_index_name`` normally: the standard helper output already
    matches the spec's literal name verbatim
    (``ck_physical_count_line_expected_nonneg``) -- no override needed.
    Every FK uses ``fk_index_name`` normally. The recommended
    ``product_id`` index uses ``idx_index_name`` with no override needed.
    The partial index ``idx_physical_count_line_unreconciled`` is likewise
    produced by plain ``idx_index_name("physical_count_line",
    "unreconciled")`` -- the helper's normal output already matches the
    spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * The application-layer rule that a non-zero ``delta`` requires
      ``reason_code_id`` before the parent count can transition to
      ``RECONCILED`` -- the spec explicitly marks this
      application-enforced, not a database-level concern (a CHECK cannot
      conditionally require one column based on another column's *and*
      the parent row's state without a trigger, which the spec does not
      call for here, unlike ``stock_adjustment``'s explicit BEFORE UPDATE
      trigger note).
    * Posting the reconciled ``stock_adjustment`` row -- an
      application-orchestrated side effect at reconciliation time, not a
      schema-level concern.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional line item), spec §13:
    *"Standard UAC"*. ``PhysicalCountLine`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every
    other UAC-using line-item table (``order_line``, ``transfer_line``).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import money_type


class PhysicalCountLine(Base, UniversalAuditColumns):
    """``T9 — physical_count_line`` — counted vs. expected quantity per product/lot (Classification: T)."""

    __tablename__ = "physical_count_line"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ---------------------------------------------------- physical_count_id
    physical_count_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "physical_count.id",
            name=fk_index_name("physical_count_line", "physical_count_id", "physical_count"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("physical_count_line", "product_id", "product"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("physical_count_line", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ----------------------------------------------------------- reason_code_id
    # Nullable -- conditional requirement (delta <> 0 at reconciliation),
    # unlike stock_adjustment.reason_code_id's unconditional NOT NULL. See
    # module docstring's dedicated section.
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "reason_code_ref.id",
            name=fk_index_name("physical_count_line", "reason_code_id", "reason_code_ref"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- expected_qty
    # No default -- application always supplies it at line-creation time
    # (snapshot from inventory_balance_snapshot).
    expected_qty: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- counted_qty
    # Nullable, no default -- genuinely unknown until physically counted.
    counted_qty: Mapped[decimal.Decimal | None] = mapped_column(
        money_type(),
        nullable=True,
    )

    # ------------------------------------------------------------------------ delta
    # Stored, application-computed. See module docstring's dedicated
    # section.
    delta: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # --------------------------------------------------------------- reconciled
    # Boolean(), mirrors stock_adjustment.threshold_marker's dual default
    # declaration.
    reconciled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
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
            "physical_count_id",
            "product_id",
            "lot_id",
            name="uq_physical_count_line",
        ),
        # CHECK: expected_qty non-negative.
        CheckConstraint(
            "expected_qty >= 0",
            name=ck_index_name("physical_count_line", "expected_nonneg"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("physical_count_line", "product_id"),
            "product_id",
        ),
        # Partial index -- unreconciled lines with a nonzero delta, per
        # count session.
        Index(
            idx_index_name("physical_count_line", "unreconciled"),
            "physical_count_id",
            postgresql_where=sa_text("reconciled = false AND delta <> 0"),
        ),
    )


__all__ = ["PhysicalCountLine"]
