"""``T7 — stock_adjustment`` ORM model (manual correction / damage / write-off request).

Authority: ``07_DATABASE_SPEC.md`` §T7 — ``T7 — stock_adjustment`` **has** a
full detailed section in the physical spec, so the spec is primary authority
here; ``06_ERD.md`` (line 56, F.2 — Stock Movement) is
secondary/corroborating only::

    T7 — stock_adjustment
    Purpose: Manual correction / damage / write-off request that, once
        applied, posts inventory_transaction rows.
    PK: id (UUID)
    FK: warehouse_id -> warehouse.id; product_id -> product.id; lot_id ->
        product_lot.id (nullable); requested_by -> app_user.id; approved_by
        -> app_user.id (nullable); reason_code_id -> reason_code_ref.id
    Column Definitions: +UAC; adjustment_number VARCHAR(40) NOT NULL;
        warehouse_id UUID NOT NULL; product_id UUID NOT NULL; lot_id UUID
        NULL; requested_by UUID NOT NULL; approved_by UUID NULL;
        reason_code_id UUID NOT NULL; adjustment_type VARCHAR(16) NOT NULL
        (POSITIVE|NEGATIVE|DAMAGE|WRITEOFF|STOCKTAKE); delta_quantity
        NUMERIC(18,4) NOT NULL (signed); state VARCHAR(16) NOT NULL DEFAULT
        'PENDING' (PENDING|APPROVED|APPLIED|REJECTED); reason_text TEXT NOT
        NULL (mandatory free-text justification); threshold_marker BOOLEAN
        NOT NULL DEFAULT false (true if this adjustment exceeded the
        auto-approval threshold)
    Unique: uq_stock_adjustment_number (adjustment_number)
    Check: ck_stock_adjustment_type (adjustment_type IN ('POSITIVE',
        'NEGATIVE','DAMAGE','WRITEOFF','STOCKTAKE')); ck_stock_adjustment_state
        (state IN ('PENDING','APPROVED','APPLIED','REJECTED'));
        ck_stock_adjustment_delta_nonzero (delta_quantity <> 0);
        ck_stock_adjustment_sign_matches_type (adjustment_type <> 'POSITIVE'
        OR delta_quantity > 0)
    Business constraints: Approval required above the system_config-defined
        threshold (checked via threshold_marker, which drives whether an
        approval_request is created -- orchestrated by the application);
        cannot drive stock negative (validated against
        inventory_balance_snapshot / the same trigger logic as T1 when
        applied); immutable once state='APPLIED' (enforced via a BEFORE
        UPDATE trigger blocking further changes to state/delta_quantity
        once APPLIED).
    Recommended Indexes: btree on warehouse_id; btree on product_id; btree
        on state
    Composite Indexes: (warehouse_id, state) -- operations queue
    Partial Indexes: idx_stock_adjustment_pending ON stock_adjustment
        (warehouse_id) WHERE state = 'PENDING'
    Soft Delete Strategy: Supported for PENDING/REJECTED rows only
        (application-enforced); APPLIED rows are effectively immutable per
        Business Constraints and should not be soft-deleted either, to
        preserve the audit chain back to the inventory_transaction it
        posted.
    Audit Strategy: Standard UAC; state transitions mirrored to audit_log;
        the APPLIED transition additionally correlates to a specific
        inventory_transaction.reference_id.

Referenced by ``06_ERD.md``'s own aggregate list as a standalone,
single-row aggregate root: *"StockAdjustment (root: stock_adjustment) --
single-row aggregate; posts to InventoryLedger on APPLIED."* -- unlike
``StockTransfer`` (which owns ``transfer_line``/``transfer_history``), this
table owns no child rows of its own; its only downstream effect is
application-orchestrated ``inventory_transaction`` posting at APPLIED time.

CRITICAL naming trap -- ``adjustment_number``'s unique constraint:
    The spec's literal constraint name is ``uq_stock_adjustment_number``.
    The project's *usual* idiom -- column-level ``unique=True`` on
    ``adjustment_number`` -- would NOT produce that name here:
    ``NAMING_CONVENTION["uq"]`` is ``uq_%(table_name)s_%(column_0_name)s``,
    so the implicit path would render
    ``uq_stock_adjustment_adjustment_number`` (table name + column name,
    both containing "adjustment", concatenated in full) -- doubling the
    word rather than collapsing it. This is the exact same latent collision
    ``order.py`` (``order_number``) and ``stock_transfer.py``
    (``transfer_number``) already documented. To get the spec's literal
    ``uq_stock_adjustment_number``, this model uses an **explicit**
    ``UniqueConstraint("adjustment_number", name=uq_index_name(
    "stock_adjustment", "number"))`` instead of column-level
    ``unique=True`` -- passing the helper a bare descriptor of ``"number"``
    (not ``"adjustment_number"``) so ``uq_index_name`` assembles ``uq_`` +
    ``stock_adjustment`` + ``number`` -> ``uq_stock_adjustment_number``
    exactly, without the doubled segment. Flagged explicitly so a future
    edit doesn't "clean this up" back to column-level ``unique=True``,
    silently reintroducing the doubled name.

``requested_by`` / ``approved_by`` -- same shape as ``stock_transfer.py``'s
own columns of the same name:
    Both target ``app_user.id``. ``requested_by`` is ``NOT NULL`` (every
    adjustment has a requester); ``approved_by`` is nullable (an adjustment
    may still be ``PENDING``, not yet approved). Neither collides with
    UAC's own ``created_by`` -- these are this table's own spec'd business
    actors, the same "business column vs. mixin audit column, same target
    table, different semantic role" situation already documented on
    ``stock_transfer.requested_by``/``approved_by``.

``adjustment_type`` / ``state`` -- ``VARCHAR(16)``, exact spec match (NOT a
placeholder):
    Both use ``state_token_type()`` -> ``VARCHAR(16)``, an *exact* match to
    the spec's own ``VARCHAR(16)`` width for both columns -- unlike
    ``stock_transfer.state``/``ownership_mode_snapshot`` (that table's own
    ``VARCHAR(20)`` spec width has no exact factory and required a
    placeholder), this table's ``VARCHAR(16)`` width is directly served by
    ``state_token_type()`` with no approximation needed. ``state`` carries
    ``NOT NULL DEFAULT 'PENDING'`` -- the same dual ``default=`` /
    ``server_default=sa_text(...)`` quoted-string-default pattern
    ``order.state`` / ``stock_transfer.state`` already establish.

``reason_text`` -- ``sqlalchemy.Text()``, ``NOT NULL`` (unlike every prior
``Text()`` column in this codebase):
    The spec's own column type is literally ``TEXT`` (unbounded) -- the
    same unbounded-text treatment ``order_status_history.note`` /
    ``transfer_history.note`` already established, using
    ``sqlalchemy.Text()`` directly since ``database/types.py`` has no
    bounded-width factory for it. This is, however, the **first**
    unbounded-``Text()`` column in this codebase declared ``NOT NULL``: the
    spec's own §4 description is explicit -- *"Mandatory free-text
    justification"* -- unlike ``order_status_history.note`` /
    ``transfer_history.note``, both of which are optional annotations on an
    otherwise-complete row. This model does not default the two prior
    ``Text()`` columns' nullable treatment onto this one; nullability is
    read from this table's own spec row.

``threshold_marker`` -- ``Boolean()``, first ``Boolean`` column reused for a
plain flag (not a partial-uniqueness driver):
    Mirrors ``currency.is_base``'s own ``Boolean()`` + dual ``default=False``
    / ``server_default=sa_text("false")`` declaration shape exactly. Unlike
    ``is_base`` (which additionally drives a partial-unique index enforcing
    "at most one true row"), ``threshold_marker`` carries no such
    constraint here -- the spec's own §5/§6 (Unique/Check Constraints)
    lists make no mention of one; it is a plain per-row flag that
    "drives whether an approval_request is created," an
    application-orchestrated decision (§7), not a database-enforced
    invariant.

Column-type choices:

* ``adjustment_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``,
  matching the spec's ``VARCHAR(40)`` -- the same
  ``.../adjustment/...``-named business-document key case
  ``business_key_type()``'s own docstring names by example.
* ``adjustment_type`` / ``state`` -- ``state_token_type()`` ->
  ``VARCHAR(16)``, exact spec match (see dedicated note above).
* ``delta_quantity`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, an exact
  match to the spec's ``NUMERIC(18,4)``. No default -- "Signed" per spec,
  the application always supplies a nonzero value at row-creation time
  (also enforced by ``ck_stock_adjustment_delta_nonzero``).
* ``reason_text`` -- ``sqlalchemy.Text()``, ``NOT NULL`` (see dedicated
  note above).
* ``threshold_marker`` -- ``sqlalchemy.Boolean()``, ``NOT NULL DEFAULT
  false`` (see dedicated note above).

Soft-delete -- added per spec, qualified treatment consistent with
``order.py`` / ``stock_transfer.py``:
    Spec §12: *"Supported for PENDING/REJECTED rows only
    (application-enforced); APPLIED rows ... should not be soft-deleted
    either."* ``deleted_at`` is added, unconditionally nullable at the
    column level -- the state-dependent restriction is a service-layer
    rule (it depends on the row's own current ``state`` value, which can
    change), not something expressible as a schema-level constraint on
    this column alone. The same treatment ``order_line.py`` gives its own
    "pre-approval only" soft-delete qualifier.

Naming convention:
    ``adjustment_number``'s unique constraint is the naming-trap case
    explained above -- ``uq_index_name("stock_adjustment", "number")``, NOT
    column-level ``unique=True``. Every CHECK below uses ``ck_index_name``
    normally: the standard helper output already matches the spec's four
    literal names verbatim (``ck_stock_adjustment_type``,
    ``ck_stock_adjustment_state``, ``ck_stock_adjustment_delta_nonzero``,
    ``ck_stock_adjustment_sign_matches_type``) -- no override needed. Every
    FK uses ``fk_index_name`` normally. The three recommended
    single-column indexes and the composite ``(warehouse_id, state)``
    index use ``idx_index_name`` (the latter with
    ``composite_descriptor``) -- no literal override needed. The partial
    index ``idx_stock_adjustment_pending`` is produced by plain
    ``idx_index_name("stock_adjustment", "pending")`` -- the helper's
    normal output already matches the spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * The ``inventory_transaction`` posting at ``state='APPLIED'`` time --
      the spec explicitly calls this application-orchestrated, not a
      database-level concern.
    * The ``BEFORE UPDATE`` immutability trigger guarding
      ``state``/``delta_quantity`` once APPLIED -- a cross-cutting
      migration/DDL-level concern the spec itself calls out as a trigger,
      not expressible as a plain CHECK constraint.
    * The "cannot drive stock negative" validation against
      ``inventory_balance_snapshot`` -- the spec explicitly calls this
      trigger/service-layer logic shared with T1, not a schema-level
      concern on this table.
    * ``approval_request`` creation driven by ``threshold_marker`` -- an
      application-orchestrated side effect, not a schema-level concern.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional request/header), spec §13:
    *"Standard UAC"*. ``StockAdjustment`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every
    other UAC-using mutable header table (``order``, ``stock_transfer``).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name, uq_index_name
from database.types import business_key_type, money_type, state_token_type


class StockAdjustment(Base, UniversalAuditColumns):
    """``T7 — stock_adjustment`` — manual correction / damage / write-off request (Classification: T)."""

    __tablename__ = "stock_adjustment"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------- adjustment_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    adjustment_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("stock_adjustment", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("stock_adjustment", "product_id", "product"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("stock_adjustment", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- requested_by
    # This table's own spec'd business actor -- distinct from UAC's mixin
    # created_by. See module docstring's dedicated section.
    requested_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("stock_adjustment", "requested_by", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- approved_by
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("stock_adjustment", "approved_by", "app_user"),
        ),
        nullable=True,
    )

    # ----------------------------------------------------------- reason_code_id
    reason_code_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "reason_code_ref.id",
            name=fk_index_name("stock_adjustment", "reason_code_id", "reason_code_ref"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ adjustment_type
    # Exact-width match to the spec's VARCHAR(16) -- not a placeholder.
    adjustment_type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- delta_quantity
    # Signed, no default -- application always supplies a nonzero value.
    delta_quantity: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- state
    # Exact-width match to the spec's VARCHAR(16). Quoted-string default
    # mirrors order.state / stock_transfer.state's own pattern.
    state: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
        default="PENDING",
        server_default=sa_text("'PENDING'"),
    )

    # ------------------------------------------------------------------ reason_text
    # sqlalchemy.Text(), NOT NULL -- mandatory free-text justification. See
    # module docstring's dedicated section (first NOT NULL Text() column in
    # this codebase).
    reason_text: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )

    # --------------------------------------------------------------- threshold_marker
    # Boolean(), mirrors currency.is_base's dual default declaration.
    threshold_marker: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    # -------------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete" section: spec §12 restricts
    # soft-deletion to PENDING/REJECTED rows -- a state-dependent,
    # service-layer rule, not a schema-level restriction on this column.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "adjustment_number") so the assembled
        # name is uq_stock_adjustment_number, not the doubled
        # uq_stock_adjustment_adjustment_number that column-level
        # unique=True's implicit convention would produce.
        UniqueConstraint(
            "adjustment_number",
            name=uq_index_name("stock_adjustment", "number"),
        ),
        # CHECK: adjustment_type vocabulary.
        CheckConstraint(
            "adjustment_type IN ('POSITIVE', 'NEGATIVE', 'DAMAGE', 'WRITEOFF', 'STOCKTAKE')",
            name=ck_index_name("stock_adjustment", "type"),
        ),
        # CHECK: state vocabulary.
        CheckConstraint(
            "state IN ('PENDING', 'APPROVED', 'APPLIED', 'REJECTED')",
            name=ck_index_name("stock_adjustment", "state"),
        ),
        # CHECK: delta_quantity must be nonzero.
        CheckConstraint(
            "delta_quantity <> 0",
            name=ck_index_name("stock_adjustment", "delta_nonzero"),
        ),
        # CHECK: a POSITIVE adjustment must carry a positive delta.
        CheckConstraint(
            "adjustment_type <> 'POSITIVE' OR delta_quantity > 0",
            name=ck_index_name("stock_adjustment", "sign_matches_type"),
        ),
        # Recommended single-column indexes.
        Index(
            idx_index_name("stock_adjustment", "warehouse_id"),
            "warehouse_id",
        ),
        Index(
            idx_index_name("stock_adjustment", "product_id"),
            "product_id",
        ),
        Index(
            idx_index_name("stock_adjustment", "state"),
            "state",
        ),
        # Composite index -- (warehouse_id, state), operations queue.
        Index(
            idx_index_name("stock_adjustment", composite_descriptor(("warehouse_id", "state"))),
            "warehouse_id",
            "state",
        ),
        # Partial index -- pending adjustments per warehouse.
        Index(
            idx_index_name("stock_adjustment", "pending"),
            "warehouse_id",
            postgresql_where=sa_text("state = 'PENDING'"),
        ),
    )


__all__ = ["StockAdjustment"]
