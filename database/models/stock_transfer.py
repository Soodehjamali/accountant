"""``T4 — stock_transfer`` ORM model (transfer header between warehouses).

Authority: ``07_DATABASE_SPEC.md`` §T4 — ``T4 — stock_transfer`` **has** a full
detailed section in the physical spec, so the spec is primary authority here;
``06_ERD.md`` (line 53, F.2 — Stock Movement) is secondary/corroborating
only::

    T4 — stock_transfer
    Purpose: Transfer header between warehouses (factory->rep, rep->factory,
        optional rep->rep) (SRS E10).
    PK: id (UUID)
    FK: source_warehouse_id -> warehouse.id; destination_warehouse_id ->
        warehouse.id; requested_by -> app_user.id; approved_by ->
        app_user.id (nullable)
    Column Definitions: +UAC; transfer_number VARCHAR(40) NOT NULL;
        source_warehouse_id UUID NOT NULL; destination_warehouse_id UUID
        NOT NULL; state VARCHAR(20) NOT NULL DEFAULT 'DRAFT'; requested_by
        UUID NOT NULL; approved_by UUID NULL; requested_at TIMESTAMPTZ NOT
        NULL DEFAULT now(); approved_at TIMESTAMPTZ NULL; dispatched_at
        TIMESTAMPTZ NULL; received_at TIMESTAMPTZ NULL;
        ownership_mode_snapshot VARCHAR(20) NOT NULL
    Unique: uq_stock_transfer_number (transfer_number)
    Check: ck_stock_transfer_state (state IN ('DRAFT','PENDING','APPROVED',
        'DISPATCHED','IN_TRANSIT','RECEIVED','PARTIAL_RECEIVED','CLOSED',
        'CANCELLED')); ck_stock_transfer_diff_warehouses
        (source_warehouse_id <> destination_warehouse_id)
    Business constraints: Double-entry posting at dispatch/receipt time (a
        TRANSFER_OUT row on source + a TRANSFER_IN row on destination in
        inventory_transaction), orchestrated by the application, not the
        database; cannot receive more than dispatched per transfer_line;
        consignment transfers do not change ownership
        (ownership_mode_snapshot is informational, not a trigger for a
        title-transfer event).
    Recommended Indexes: btree on source_warehouse_id; btree on
        destination_warehouse_id; btree on state
    Composite Indexes: (state, requested_at) -- operations queue view
    Partial Indexes: idx_stock_transfer_open ON stock_transfer
        (destination_warehouse_id) WHERE state NOT IN ('CLOSED','CANCELLED')
    Soft Delete Strategy: Supported, though a transfer is normally
        terminated via state='CANCELLED', not deletion.
    Audit Strategy: Standard UAC; state transitions additionally captured
        in transfer_history (T6).

This is the aggregate root for the StockTransfer aggregate (``06_ERD.md``
PART on aggregate boundaries: *"StockTransfer (root: stock_transfer) --
owns transfer_line, transfer_history"*) -- ``transfer_line`` (T5) and
``transfer_history`` (T6) both carry a ``stock_transfer_id`` FK back to this
table.

Both source/destination FKs target ``warehouse`` -- NOT a reserved word:
    Unlike ``order.py``'s own reserved-word table-name situation,
    ``warehouse`` is an ordinary identifier -- both ``source_warehouse_id``
    and ``destination_warehouse_id`` reference the plain literal
    ``"warehouse.id"`` string with no quoting concerns.

CRITICAL naming trap -- ``transfer_number``'s unique constraint:
    The spec's literal constraint name is ``uq_stock_transfer_number``. The
    project's *usual* idiom -- column-level ``unique=True`` on
    ``transfer_number`` -- would NOT produce that name here:
    ``NAMING_CONVENTION["uq"]`` is ``uq_%(table_name)s_%(column_0_name)s``,
    so the implicit path would render ``uq_stock_transfer_transfer_number``
    (table name + column name, both containing "transfer", concatenated in
    full) -- doubling the word rather than collapsing it. This is the exact
    same latent collision ``order.py`` already documented for
    ``order.order_number`` (table name embeds a prefix of the column name).
    To get the spec's literal ``uq_stock_transfer_number``, this model uses
    an **explicit** ``UniqueConstraint("transfer_number",
    name=uq_index_name("stock_transfer", "number"))`` instead of
    column-level ``unique=True`` -- passing the helper a bare descriptor of
    ``"number"`` (not ``"transfer_number"``) so ``uq_index_name`` assembles
    ``uq_`` + ``stock_transfer`` + ``number`` -> ``uq_stock_transfer_number``
    exactly, without the doubled segment. Flagged explicitly so a future
    edit doesn't "clean this up" back to column-level ``unique=True``,
    silently reintroducing the doubled name.

``state`` -- ``VARCHAR(20)`` per spec, placeholder width, NOT an exact match:
    Unlike ``order_status_history.from_state``/``to_state`` (an exact
    ``VARCHAR(24)`` match to that table's own spec width), this column's
    spec width is ``VARCHAR(20)`` and no ``database.types`` factory produces
    exactly 20 characters (``state_token_type()`` -> 16,
    ``state_token_long_type()`` -> 24). ``state_token_long_type()`` is used
    as the closest existing factory -- the same placeholder treatment
    ``order.py`` already gave its own ``sales_channel`` / ``fulfillment_mode``
    columns. ``NOT NULL DEFAULT 'DRAFT'`` -- mirrors ``order.state``'s own
    dual ``default="DRAFT"`` / ``server_default=sa_text("'DRAFT'")``
    quoted-string-default pattern exactly.

``ownership_mode_snapshot`` -- placeholder width, deliberately NO CHECK:
    Spec column type is ``VARCHAR(20)`` -- same placeholder situation as
    ``state`` above, so ``state_token_long_type()`` is used here too. The
    spec's own §6 Check Constraints list for this table names exactly two
    constraints (``ck_stock_transfer_state`` and
    ``ck_stock_transfer_diff_warehouses``) -- a vocabulary CHECK on
    ``ownership_mode_snapshot`` is NOT among them, and the spec's own prose
    describes it as informational ("Snapshot of source/destination
    ownership mode at creation" -- §4; "informational, not a trigger for a
    title-transfer event" -- §7). This mirrors ``order_line.fulfillment_mode``'s
    own "snapshot of an already-validated value, not independently
    validated" treatment: no CHECK is added here even though
    ``warehouse.ownership_mode`` (the value being snapshotted) has one.

``requested_by`` / ``approved_by`` -- real FKs, distinct nullability:
    Both target ``app_user.id``. ``requested_by`` is ``NOT NULL`` (every
    transfer has a requester); ``approved_by`` is nullable (a transfer may
    still be ``DRAFT``/``PENDING``, not yet approved). Neither collides with
    UAC's own ``created_by`` -- these are this table's own spec'd business
    actors (who requested / who approved this specific transfer), the same
    "business column vs. mixin audit column, same target table, different
    semantic role" situation already documented on
    ``order_status_history.actor_user_id`` / ``stock_reservation.reserved_by``.

Timestamps -- ``requested_at`` defaulted, the rest plain nullable:
    ``requested_at`` is ``NOT NULL DEFAULT now()`` (mirrors
    ``order.ordered_at``). ``approved_at`` / ``dispatched_at`` /
    ``received_at`` are nullable with no default -- each is set once,
    later, by the application as the transfer progresses through its state
    machine (mirrors ``order.shipped_at`` / ``invoiced_at`` / ``paid_at``).

Column-type choices:

* ``transfer_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``, an
  exact match to the spec's ``VARCHAR(40)``. This is precisely the
  ``.../transfer/...`` business-document key case
  ``business_key_type()``'s own docstring names by example.
* ``state`` / ``ownership_mode_snapshot`` -- ``state_token_long_type()`` ->
  ``VARCHAR(24)``, placeholder for the spec's ``VARCHAR(20)`` (see dedicated
  notes above; no exact 20-width factory exists).
* ``requested_at`` / ``approved_at`` / ``dispatched_at`` / ``received_at`` --
  ``DateTime(timezone=True)``.

Soft-delete -- added per spec, same qualified treatment as ``order.py``:
    Spec §12: *"Supported, though a transfer is normally terminated via
    state='CANCELLED', not deletion."* ``deleted_at`` is added,
    unconditionally nullable at the column level -- the "normally prefer
    CANCELLED" guidance is a service-layer/operational preference, not a
    schema-level restriction, the same treatment ``order.py`` gives its own
    analogous soft-delete note.

Naming convention:
    ``transfer_number``'s unique constraint is the naming-trap case
    explained above -- ``uq_index_name("stock_transfer", "number")``, NOT
    column-level ``unique=True``. Both CHECKs use ``ck_index_name``
    normally: the standard helper output already matches the spec's two
    literal names verbatim (``ck_stock_transfer_state``,
    ``ck_stock_transfer_diff_warehouses``) -- no override needed. Every FK
    uses ``fk_index_name`` normally. The three recommended single-column
    indexes and the composite ``(state, requested_at)`` index use
    ``idx_index_name`` (the latter with ``composite_descriptor``) -- no
    literal override needed for any of them. The partial index
    ``idx_stock_transfer_open`` is produced by plain
    ``idx_index_name("stock_transfer", "open")`` -- the helper's normal
    output already matches the spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * The double-entry ``inventory_transaction`` posting at dispatch/receipt
      time -- the spec explicitly calls this application-orchestrated, not a
      database-level concern.
    * The "cannot receive more than dispatched per transfer_line" business
      constraint -- enforced on ``transfer_line`` itself (T5's own CHECK
      constraints), not on this header table.
    * Range partitioning by ``requested_at`` (yearly) -- spec marks this a
      future/conditional physical-design decision, not required now.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional header), spec §13: *"Standard
    UAC"*. ``StockTransfer`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every other
    UAC-using mutable header table (``order``, ``order_line``).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name, uq_index_name
from database.types import business_key_type, state_token_long_type


class StockTransfer(Base, UniversalAuditColumns):
    """``T4 — stock_transfer`` — transfer header between warehouses (Classification: T)."""

    __tablename__ = "stock_transfer"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------- transfer_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    transfer_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # --------------------------------------------------- source_warehouse_id
    source_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("stock_transfer", "source_warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ---------------------------------------------- destination_warehouse_id
    destination_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("stock_transfer", "destination_warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------------- state
    # Placeholder width -- see module docstring's dedicated section.
    # Quoted-string default mirrors order.state's own pattern.
    state: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
        default="DRAFT",
        server_default=sa_text("'DRAFT'"),
    )

    # ------------------------------------------------------------- requested_by
    # This table's own spec'd business actor -- distinct from UAC's mixin
    # created_by. See module docstring's dedicated section.
    requested_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("stock_transfer", "requested_by", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- approved_by
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("stock_transfer", "approved_by", "app_user"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- requested_at
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ----------------------------------------- approved/dispatched/received_at
    approved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dispatched_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    received_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --------------------------------------------------- ownership_mode_snapshot
    # Placeholder width, deliberately no CHECK -- see module docstring's
    # dedicated section.
    ownership_mode_snapshot: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete" section: spec §12 supports soft
    # delete, though CANCELLED state is the normal termination path.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "transfer_number") so the assembled
        # name is uq_stock_transfer_number, not the doubled
        # uq_stock_transfer_transfer_number that column-level unique=True's
        # implicit convention would produce.
        UniqueConstraint(
            "transfer_number",
            name=uq_index_name("stock_transfer", "number"),
        ),
        # CHECK: full 9-value TransferState vocabulary, transcribed verbatim
        # from the spec. Reused verbatim by transfer_history.py's own
        # ck_transfer_history_states CHECK, to guarantee the two can never
        # silently drift apart.
        CheckConstraint(
            "state IN ("
            "'DRAFT', 'PENDING', 'APPROVED', 'DISPATCHED', 'IN_TRANSIT', "
            "'RECEIVED', 'PARTIAL_RECEIVED', 'CLOSED', 'CANCELLED'"
            ")",
            name=ck_index_name("stock_transfer", "state"),
        ),
        # CHECK: source and destination warehouses must differ.
        CheckConstraint(
            "source_warehouse_id <> destination_warehouse_id",
            name=ck_index_name("stock_transfer", "diff_warehouses"),
        ),
        # Recommended single-column indexes.
        Index(
            idx_index_name("stock_transfer", "source_warehouse_id"),
            "source_warehouse_id",
        ),
        Index(
            idx_index_name("stock_transfer", "destination_warehouse_id"),
            "destination_warehouse_id",
        ),
        Index(
            idx_index_name("stock_transfer", "state"),
            "state",
        ),
        # Composite index -- (state, requested_at), operations queue view.
        Index(
            idx_index_name("stock_transfer", composite_descriptor(("state", "requested_at"))),
            "state",
            "requested_at",
        ),
        # Partial index -- open (not closed/cancelled) transfers per
        # destination warehouse.
        Index(
            idx_index_name("stock_transfer", "open"),
            "destination_warehouse_id",
            postgresql_where=sa_text("state NOT IN ('CLOSED', 'CANCELLED')"),
        ),
    )


__all__ = ["StockTransfer"]
