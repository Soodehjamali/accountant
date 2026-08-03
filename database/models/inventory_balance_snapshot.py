"""``T3 — inventory_balance_snapshot`` ORM model (Projection — Non-Authoritative).

Authority: ``07_DATABASE_SPEC.md`` §T3 — ``T3 — inventory_balance_snapshot``
**has** a full detailed section in the physical spec, so the spec is
primary authority here; ``06_ERD.md`` (F.1, "``inventory_balance_snapshot``
(T3) is a 1:1 derived projection per (warehouse, product, lot)") is
secondary/corroborating only::

    T3 — inventory_balance_snapshot (Projection — Non-Authoritative)
    Purpose: Read-optimized, non-authoritative cache of current stock,
        derived entirely from inventory_transaction.
    PK: id (UUID)
    FK: warehouse_id -> warehouse.id; product_id -> product.id;
        lot_id -> product_lot.id (nullable)
    Unique: uq_inventory_balance_snapshot (warehouse_id, product_id,
        lot_id) -- partial unique index scoped to non-deleted rows,
        realized as two partial unique indexes (WHERE lot_id IS NULL /
        WHERE lot_id IS NOT NULL) since a NULLable column in a UNIQUE
        constraint already treats NULLs as distinct.
    Check: none -- intentionally no CHECK (quantity_on_hand >= 0);
        this table is explicitly allowed to be transiently stale/
        inconsistent as a cache, correctness is enforced upstream on
        inventory_transaction, not here.
    Business constraints: never written to directly by application
        business logic -- only by the reconciliation/projection job;
        quantity_on_hand must equal SUM(inventory_transaction.
        signed_quantity WHERE is_reversed = false) for the key as of the
        last reconciliation -- verified, not enforced (no synchronous
        trigger on the ledger).
    Soft Delete Strategy: Not applicable -- rows are upserted/rebuilt,
        not soft-deleted.
    Audit Strategy: None beyond last_reconciled_at / last_transaction_seq
        -- this table is a cache, not an audited business record; the
        audit trail lives entirely in inventory_transaction.
    Notes: Refresh strategy: incremental (event-bus-triggered off
        inventory_transaction insert, batched), not synchronous-per-write.

Audit-mixin decision -- NEITHER UAC NOR AAC:
    This is the first V-classified (derived-projection) table implemented in
    this codebase, so the audit-mixin choice is spelled out here explicitly
    rather than following an established V-table precedent (there is none
    yet).

    The spec's own Audit Strategy row (§13) is unambiguous: *"None beyond
    last_reconciled_at/last_transaction_seq -- this table is a cache, not an
    audited business record."* Both existing mixins exist to answer "who
    changed this row and when" for a *business record* -- a fact someone is
    accountable for. This table has no such fact to record:

    * **UAC is wrong** -- UAC's whole point is a per-row human/system
      accountability trail (``created_by`` NOT NULL, ``updated_by``
      nullable-for-system, optimistic-locking ``version``) for a row that
      represents a *decision or transaction* someone is responsible for.
      Every field this table has instead comes from re-deriving a
      computation off ``inventory_transaction`` -- there is no "actor" in
      the business sense, only a reconciliation job re-running the same
      arithmetic on a schedule. Attaching ``created_by``/``updated_by`` FKs
      to ``app_user`` would misrepresent a scheduled recomputation as a
      user-attributable action, and ``version``-based optimistic locking
      would fight the job's own upsert/rebuild strategy (§12: "rows are
      upserted/rebuilt, not soft-deleted") rather than protect a genuine
      concurrent-edit race the way it does on ``order``/``order_line``/
      ``stock_reservation``.
    * **AAC is also wrong**, for a more specific reason than "it's the
      lighter-weight mixin": AAC models an *append-only, immutable-once-
      written* row (``shipment_status_history``, ``order_status_history`` --
      a new row per event, never touched again). This table is the exact
      opposite: the *same* row (one per ``(warehouse_id, product_id,
      lot_id)``) is repeatedly overwritten in place every time the
      reconciliation job runs (§12 again: "upserted/rebuilt"). Giving it
      AAC's single ``created_at``/``created_by`` pair would silently lie
      about the row's own history -- ``created_at`` would freeze at the
      row's *first* upsert while every subsequent recompute mutates the
      same row underneath it, with no column recording *when* the current
      values were actually produced. That gap is precisely what
      ``last_reconciled_at`` (this table's own bespoke column, not a mixin
      field) exists to fill correctly: it is refreshed on every
      reconciliation run, not just the first one.

    **Conclusion:** neither mixin is used. The table instead carries its own
    two purpose-built provenance columns as ordinary, spec'd business
    columns -- ``last_reconciled_at`` (when the cache was last verified
    against T1) and ``last_transaction_seq`` (how far into the ledger that
    verification reached) -- which are the *correct*, narrower audit
    surface for a re-derivable cache, not a placeholder for the generic
    mixins. This keeps ``StockReservation``/``Order``/``OrderLine``'s UAC
    usage and this table's mixin-free columns each honest about what kind
    of row they represent, rather than forcing every T-classified table
    through the same two audit shapes regardless of fit.

No FK deviation -- all three FKs are real from the outset:
    Same situation as ``T2 — stock_reservation``: ``warehouse``, ``product``,
    and ``product_lot`` all already exist in this codebase, so
    ``warehouse_id`` / ``product_id`` / ``lot_id`` are declared as real
    ``ForeignKey()`` constraints from the start -- no deferred-FK section to
    write for this table.

CRITICAL naming trap -- the unique constraint becomes TWO partial unique
indexes, not one ``UniqueConstraint``:
    The spec's literal name ``uq_inventory_balance_snapshot`` describes a
    single logical uniqueness rule, but the spec's own parenthetical spells
    out *why* it cannot be a single ordinary ``UniqueConstraint``: PostgreSQL
    (like SQL generally) treats every ``NULL`` in a unique column as
    distinct from every other ``NULL``, so an ordinary
    ``UniqueConstraint("warehouse_id", "product_id", "lot_id")`` would
    silently allow unlimited duplicate ``(warehouse_id, product_id,
    lot_id=NULL)`` rows -- exactly the un-lotted-product case this table
    needs to dedupe correctly. The spec's fix is two partial unique
    indexes, split on ``lot_id IS NULL`` vs ``lot_id IS NOT NULL``, so this
    model declares two ``Index(unique=True, postgresql_where=...)`` objects
    instead of a ``UniqueConstraint``. SQLAlchemy's ``UniqueConstraint``
    has no ``postgresql_where``/partial-index support at all, so a
    ``Index(unique=True, ...)`` pair is the only way to express this,
    independent of any naming-trap consideration.

    Naming for these two indexes: since the spec gives one base name for two
    physical indexes, this model suffixes ``uq_inventory_balance_snapshot``
    with ``_lot_null`` / ``_lot_not_null`` via a plain ``uq_index_name(table,
    descriptor)`` call per index (``uq_inventory_balance_snapshot_lot_null``
    / ``uq_inventory_balance_snapshot_lot_not_null``) -- not literally spec'd
    character-for-character (the spec gives one name for the concept), but
    the closest faithful rendering of the spec's own stated intent once one
    logical constraint must become two physical index objects.

Composite/partial index note -- deliberately NO separate composite index:
    Spec §9 ("Composite Indexes: (warehouse_id, product_id, lot_id) --
    doubles as the unique constraint's supporting index") and §10 ("Partial
    Indexes: see Unique Constraints above") both point back to the same two
    partial unique indexes above -- each of the two IS a composite index
    over exactly those three columns. This model therefore does NOT add a
    third, non-unique composite ``Index`` on top; doing so would duplicate
    index coverage the two partial unique indexes already provide.

Naming convention:
    The two partial unique indexes are the naming-trap case explained above.
    The recommended single-column index on ``product_id`` uses
    ``idx_index_name`` normally -- no override needed. Every FK uses
    ``fk_index_name`` normally. There are no CHECK constraints on this table
    (spec §6: intentionally none).

Column-type choices:

* ``quantity_on_hand`` / ``quantity_reserved`` / ``quantity_available`` --
  ``money_type()`` -> ``NUMERIC(18, 4)``, an exact match to the spec's
  ``NUMERIC(18,4)`` for all three. All three carry ``DEFAULT 0`` per spec,
  mirroring ``order.py`` / ``order_line.py``'s own money-column
  ``default=0`` + ``server_default=sa_text("0")`` dual-declaration pattern.
  ``quantity_available`` is stored (an ordinary column the reconciliation
  job writes), **not** a SQL-generated column -- the spec is explicit
  that it is "stored (not generated) since it is refreshed atomically
  alongside the other two", i.e. the application/job computes and writes
  all three together rather than relying on a ``GENERATED ALWAYS AS``
  expression.
* ``last_reconciled_at`` -- ``DateTime(timezone=True)``, ``NOT NULL``,
  ``server_default=func.now()`` -- mirrors every other ``now()``-defaulted
  timestamp already in this codebase (e.g. ``order.ordered_at``).
* ``last_transaction_seq`` -- plain ``sqlalchemy.BigInteger()`` (spec:
  ``BIGINT``), the same factory ``inventory_transaction.sequence_no``
  already uses for its own ``BIGINT`` column. ``NOT NULL DEFAULT 0``.

Out of scope for this model (not implemented here):
    * The reconciliation/projection job itself (event-bus-triggered,
      incremental, batched per the Notes) -- an application/worker-level
      concern, not a schema one.
    * Any Alembic migration.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.naming import fk_index_name, idx_index_name, uq_index_name
from database.types import money_type


class InventoryBalanceSnapshot(Base):
    """``T3 — inventory_balance_snapshot`` — read-optimized, non-authoritative cache of current stock, derived from ``inventory_transaction`` (Classification: V).

    No audit mixin (neither UAC nor AAC) -- see the module docstring's
    dedicated "Audit-mixin decision" section for the full reasoning. This
    table's own ``last_reconciled_at`` / ``last_transaction_seq`` columns
    are its correct, narrower audit surface, not a placeholder for the
    generic mixins.
    """

    __tablename__ = "inventory_balance_snapshot"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("inventory_balance_snapshot", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("inventory_balance_snapshot", "product_id", "product"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("inventory_balance_snapshot", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------- quantity columns
    quantity_on_hand: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    quantity_reserved: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    # Stored (not generated) -- see module docstring's column-type-choices
    # note. Refreshed atomically alongside the two columns above by the
    # reconciliation job, not computed by the database.
    quantity_available: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ----------------------------------------------------------- last_reconciled_at
    last_reconciled_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ---------------------------------------------------- last_transaction_seq
    last_transaction_seq: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    __table_args__ = (
        # Partial unique index #1 -- un-lotted rows (lot_id IS NULL).
        # See module docstring's "CRITICAL naming trap" section for why
        # this is TWO partial unique indexes, not one UniqueConstraint.
        Index(
            uq_index_name("inventory_balance_snapshot", "lot_null"),
            "warehouse_id",
            "product_id",
            "lot_id",
            unique=True,
            postgresql_where=sa_text("lot_id IS NULL"),
        ),
        # Partial unique index #2 -- lotted rows (lot_id IS NOT NULL).
        Index(
            uq_index_name("inventory_balance_snapshot", "lot_not_null"),
            "warehouse_id",
            "product_id",
            "lot_id",
            unique=True,
            postgresql_where=sa_text("lot_id IS NOT NULL"),
        ),
        # Recommended single-column index. No separate composite index is
        # added -- see module docstring's "Composite/partial index note".
        Index(
            idx_index_name("inventory_balance_snapshot", "product_id"),
            "product_id",
        ),
    )


__all__ = ["InventoryBalanceSnapshot"]
