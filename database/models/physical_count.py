"""``T8 — physical_count`` ORM model (stocktake session header).

Authority: ``07_DATABASE_SPEC.md`` §T8 — ``T8 — physical_count`` **has** a
full detailed section in the physical spec, so the spec is primary authority
here; ``06_ERD.md`` (line 58, F.3 — Physical Count) is
secondary/corroborating only::

    T8 — physical_count
    Purpose: Stocktake session header.
    PK: id (UUID)
    FK: warehouse_id -> warehouse.id; opened_by -> app_user.id; closed_by ->
        app_user.id (nullable)
    Column Definitions: +UAC; count_number VARCHAR(40) NOT NULL;
        warehouse_id UUID NOT NULL; opened_by UUID NOT NULL; closed_by UUID
        NULL; state VARCHAR(16) NOT NULL DEFAULT 'OPEN'
        (OPEN|COUNTING|RECONCILED|CLOSED); scope VARCHAR(255) NULL
        (free-text scope -- full warehouse, category subset, etc.);
        opened_at TIMESTAMPTZ NOT NULL DEFAULT now(); closed_at TIMESTAMPTZ
        NULL
    Unique: uq_physical_count_number (count_number)
    Check: ck_physical_count_state (state IN ('OPEN','COUNTING',
        'RECONCILED','CLOSED'))
    Business constraints: Only one OPEN/COUNTING count per warehouse at a
        time -- enforced via partial unique index; closing reconciles all
        physical_count_line deltas into stock_adjustment/
        inventory_transaction rows, orchestrated by the application.
    Recommended Indexes: btree on warehouse_id
    Composite Indexes: none beyond the partial index
    Partial Indexes: uq_physical_count_one_open ON physical_count
        (warehouse_id) WHERE state IN ('OPEN','COUNTING')
    Soft Delete Strategy: Supported for erroneous sessions only, not for
        CLOSED counts
    Audit Strategy: Standard UAC

This is the aggregate root for the PhysicalCount aggregate (``06_ERD.md``:
*"PhysicalCount (root: physical_count) -- owns physical_count_line"*) --
``physical_count_line`` (T9) carries a ``physical_count_id`` FK back to this
table.

CRITICAL naming trap -- ``count_number``'s unique constraint:
    The spec's literal constraint name is ``uq_physical_count_number``. The
    project's *usual* idiom -- column-level ``unique=True`` on
    ``count_number`` -- would NOT produce that name here:
    ``NAMING_CONVENTION["uq"]`` is ``uq_%(table_name)s_%(column_0_name)s``,
    so the implicit path would render ``uq_physical_count_count_number``
    (table name + column name, both containing "count", concatenated in
    full) -- doubling the word rather than collapsing it. This is the exact
    same latent collision ``order.py`` (``order_number``),
    ``stock_transfer.py`` (``transfer_number``), and ``stock_adjustment.py``
    (``adjustment_number``) already documented. To get the spec's literal
    ``uq_physical_count_number``, this model uses an **explicit**
    ``UniqueConstraint("count_number", name=uq_index_name("physical_count",
    "number"))`` instead of column-level ``unique=True`` -- passing the
    helper a bare descriptor of ``"number"`` (not ``"count_number"``) so
    ``uq_index_name`` assembles ``uq_`` + ``physical_count`` + ``number`` ->
    ``uq_physical_count_number`` exactly, without the doubled segment.
    Flagged explicitly so a future edit doesn't "clean this up" back to
    column-level ``unique=True``, silently reintroducing the doubled name.

``opened_by`` / ``closed_by`` -- same "business actor, distinct from UAC's
``created_by``" pattern established elsewhere:
    Both target ``app_user.id``. ``opened_by`` is ``NOT NULL`` (every
    session has an opener); ``closed_by`` is nullable (a session may still
    be ``OPEN``/``COUNTING``/``RECONCILED``, not yet closed). Same
    "business column vs. mixin audit column, same target table, different
    semantic role" situation already documented on
    ``stock_transfer.requested_by``/``approved_by`` and
    ``stock_adjustment.requested_by``/``approved_by``.

``state`` -- ``VARCHAR(16)``, exact spec match (NOT a placeholder):
    ``state_token_type()`` -> ``VARCHAR(16)``, an *exact* match to the
    spec's own width -- the same exact-match treatment
    ``stock_adjustment.adjustment_type``/``state`` already receive.
    ``NOT NULL DEFAULT 'OPEN'`` -- the same dual ``default=`` /
    ``server_default=sa_text(...)`` quoted-string-default pattern
    ``order.state`` / ``stock_transfer.state`` / ``stock_adjustment.state``
    already establish.

``scope`` -- ``description_type()``, exact width match, nullable free text:
    Spec type is ``VARCHAR(255)`` -- ``description_type()`` -> exactly
    ``VARCHAR(255)``, a direct (not placeholder) fit: its own docstring's
    "description / line description" purpose statement is a close semantic
    match to this column's "free-text scope (full warehouse, category
    subset, etc.)" description, and the width matches the spec exactly.
    Nullable per spec -- most counts likely default to full-warehouse scope
    with no free-text override needed.

Business Constraint §7 -- "only one OPEN/COUNTING count per warehouse":
    Enforced as the spec itself directs -- *"via partial unique index"* --
    not a CHECK constraint (a CHECK cannot see other rows). The spec's own
    §10 gives this partial index a **``uq_``-prefixed** literal name
    (``uq_physical_count_one_open``), unlike every other partial index in
    this codebase so far (which use the ``idx_`` prefix, e.g.
    ``idx_stock_transfer_open`` / ``idx_stock_adjustment_pending``) --
    reflecting that this one is a genuine *uniqueness* constraint
    physically implemented as a partial ``UNIQUE INDEX``, not a mere
    query-optimization index. This model reuses ``uq_index_name`` (not
    ``idx_index_name``) for it, passing the bare descriptor
    ``"one_open"`` so the assembled name is
    ``uq_physical_count_one_open`` exactly -- matching the spec's literal
    name verbatim, and consistent with ``currency.py``'s own existing
    "partial unique ``Index(..., unique=True, postgresql_where=...)``"
    idiom for the "exactly one/at-most-one" invariant pattern
    (``idx_currency_one_base``, though that one happens to use the
    ``idx_`` prefix since the spec did not give it a ``uq_``-prefixed
    name -- this table's spec explicitly does).

Column-type choices:

* ``count_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``, matching
  the spec's ``VARCHAR(40)`` -- the same business-document key case
  ``business_key_type()``'s own docstring names by example.
* ``state`` -- ``state_token_type()`` -> ``VARCHAR(16)``, exact spec match
  (see dedicated note above).
* ``scope`` -- ``description_type()`` -> ``VARCHAR(255)``, exact spec match
  (see dedicated note above).
* ``opened_at`` / ``closed_at`` -- ``DateTime(timezone=True)``.
  ``opened_at`` is ``NOT NULL DEFAULT now()`` (mirrors
  ``order.ordered_at`` / ``stock_transfer.requested_at``); ``closed_at`` is
  nullable with no default -- set once, later, when the session closes
  (mirrors ``stock_transfer.dispatched_at``/``received_at``).

Soft-delete -- added per spec, qualified treatment consistent with
``order.py`` / ``stock_transfer.py`` / ``stock_adjustment.py``:
    Spec §12: *"Supported for erroneous sessions only, not for CLOSED
    counts."* ``deleted_at`` is added, unconditionally nullable at the
    column level -- the state-dependent restriction ("not for CLOSED
    counts") is a service-layer rule (it depends on the row's own current
    ``state`` value, which can change over the row's lifetime), not
    something expressible as a schema-level constraint on this column
    alone. The same treatment ``stock_adjustment.py`` gives its own
    state-conditional soft-delete qualifier.

Naming convention:
    ``count_number``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("physical_count", "number")``, NOT
    column-level ``unique=True``. The single CHECK uses ``ck_index_name``
    normally: the standard helper output already matches the spec's
    literal name verbatim (``ck_physical_count_state``) -- no override
    needed. Every FK uses ``fk_index_name`` normally. The recommended
    ``warehouse_id`` index uses ``idx_index_name`` with no override needed.
    The partial *unique* index uses ``uq_index_name`` (not
    ``idx_index_name``) -- see the dedicated "Business Constraint §7"
    section above for why.

Out of scope for this model (not implemented here):
    * The closing-time reconciliation of ``physical_count_line`` deltas
      into ``stock_adjustment``/``inventory_transaction`` rows -- the spec
      explicitly calls this application-orchestrated, not a
      database-level concern.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional header), spec §13:
    *"Standard UAC"*. ``PhysicalCount`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every
    other UAC-using mutable header table (``order``, ``stock_transfer``,
    ``stock_adjustment``).
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
from database.naming import ck_index_name, fk_index_name, idx_index_name, uq_index_name
from database.types import business_key_type, description_type, state_token_type


class PhysicalCount(Base, UniversalAuditColumns):
    """``T8 — physical_count`` — stocktake session header (Classification: T)."""

    __tablename__ = "physical_count"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------- count_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    count_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("physical_count", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------- opened_by
    # This table's own spec'd business actor -- distinct from UAC's mixin
    # created_by. See module docstring's dedicated section.
    opened_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("physical_count", "opened_by", "app_user"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------- closed_by
    closed_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("physical_count", "closed_by", "app_user"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------------- state
    # Exact-width match to the spec's VARCHAR(16). Quoted-string default
    # mirrors order.state / stock_transfer.state / stock_adjustment.state.
    state: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
        default="OPEN",
        server_default=sa_text("'OPEN'"),
    )

    # ---------------------------------------------------------------------- scope
    # Exact-width match to the spec's VARCHAR(255). See module docstring's
    # dedicated section. Nullable per spec.
    scope: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )

    # --------------------------------------------------------------- opened_at
    opened_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # --------------------------------------------------------------- closed_at
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete" section: spec §12 restricts
    # soft-deletion to erroneous (non-CLOSED) sessions -- a state-dependent,
    # service-layer rule, not a schema-level restriction on this column.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "count_number") so the assembled name
        # is uq_physical_count_number, not the doubled
        # uq_physical_count_count_number that column-level unique=True's
        # implicit convention would produce.
        UniqueConstraint(
            "count_number",
            name=uq_index_name("physical_count", "number"),
        ),
        # CHECK: state vocabulary.
        CheckConstraint(
            "state IN ('OPEN', 'COUNTING', 'RECONCILED', 'CLOSED')",
            name=ck_index_name("physical_count", "state"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("physical_count", "warehouse_id"),
            "warehouse_id",
        ),
        # Partial UNIQUE index -- at most one OPEN/COUNTING count per
        # warehouse. Uses uq_index_name (not idx_index_name) because the
        # spec itself gives this a uq_-prefixed literal name -- see module
        # docstring's "Business Constraint §7" section.
        Index(
            uq_index_name("physical_count", "one_open"),
            "warehouse_id",
            unique=True,
            postgresql_where=sa_text("state IN ('OPEN', 'COUNTING')"),
        ),
    )


__all__ = ["PhysicalCount"]
