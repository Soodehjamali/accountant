"""``T14 — shipment`` ORM model (physical shipment tied to an order).

Authority: ``07_DATABASE_SPEC.md`` §T14 — ``T14 — shipment`` **has** a full
detailed section in the physical spec, so the spec is primary authority here;
``06_ERD.md`` (F.5 — Fulfillment / Shipping, T14 line) is
secondary/corroborating only::

    T14 — shipment
    Purpose: Physical shipment tied to an order (SRS E17, BRF §10).
    PK: id (UUID)
    FK: order_id -> order.id; source_warehouse_id -> warehouse.id;
        carrier_id -> carrier.id; shipped_by -> app_user.id (nullable)
    Column Definitions: +UAC; shipment_number VARCHAR(40) NOT NULL;
        order_id UUID NOT NULL; source_warehouse_id UUID NOT NULL;
        carrier_id UUID NOT NULL; shipped_by UUID NULL; state VARCHAR(16)
        NOT NULL DEFAULT 'CREATED'; shipped_at TIMESTAMPTZ NULL;
        delivered_at TIMESTAMPTZ NULL; shipping_cost NUMERIC(18,4) NOT NULL
        DEFAULT 0; shipping_currency_id UUID NOT NULL; shipping_payer
        VARCHAR(16) NOT NULL; tracking_number VARCHAR(80) NULL
    Unique: uq_shipment_number (shipment_number)
    Check: ck_shipment_state (state IN ('CREATED','PICKING','PACKED',
        'DISPATCHED','IN_TRANSIT','DELIVERED','FAILED'));
        ck_shipment_payer (shipping_payer IN
        ('CUSTOMER','REPRESENTATIVE','FACTORY'));
        ck_shipment_cost_nonneg (shipping_cost >= 0)
    Business constraints: LOCAL/REP_LOCAL fulfillment posts a SALE_OUT
        inventory_transaction; DIRECT/FACTORY_DIRECT fulfillment posts a
        FACTORY_DIRECT_SHIPMENT movement instead -- orchestrated by the
        application at the DISPATCHED transition; reaching DELIVERED
        advances the parent order.state toward SHIPPED/COMPLETED per the
        order state machine (application-orchestrated, cross-table).
    Recommended Indexes: btree on order_id; btree on carrier_id; btree on
        tracking_number
    Composite Indexes: none beyond above
    Partial Indexes: idx_shipment_active ON shipment (state) WHERE state
        NOT IN ('DELIVERED','FAILED') -- active-shipment dashboard
    Soft Delete Strategy: Supported, though a failed shipment should use
        state='FAILED' rather than deletion
    Audit Strategy: Standard UAC; state transitions mirrored to
        shipment_status_history (T16)

Root of the Shipment aggregate (``06_ERD.md`` PART on aggregate boundaries:
*"Shipment (root: shipment) -- owns shipment_line, shipment_status_history"*)
-- ``shipment_line`` (T15) and ``shipment_status_history`` (T16) both carry a
``shipment_id`` FK back to this table.

Reserved-word FK target -- ``order_id -> order.id``:
    ``order`` is a reserved SQL keyword; this table reuses the same literal
    ``"order.id"`` string every other FK into that table already uses
    (``order_line.order_id``, ``order_status_history.order_id``,
    ``stock_reservation.order_id``) -- SQLAlchemy's dialect-aware identifier
    preparer auto-quotes the reserved identifier wherever it's emitted, no
    extra configuration needed here.

Non-reserved-word FK targets -- ``source_warehouse_id -> warehouse.id`` /
``carrier_id -> carrier.id`` / ``shipped_by -> app_user.id``:
    All three are ordinary identifiers, no quoting concerns.

CRITICAL naming trap -- ``shipment_number``'s unique constraint:
    The spec's literal constraint name is ``uq_shipment_number``. Column-level
    ``unique=True`` on ``shipment_number`` would NOT produce that name:
    ``NAMING_CONVENTION["uq"]`` is ``uq_%(table_name)s_%(column_0_name)s``, so
    the implicit path would render ``uq_shipment_shipment_number`` (table
    name + column name, both containing "shipment", concatenated in full) --
    doubling the word rather than collapsing it. This is the exact same
    latent collision ``stock_transfer.py`` already documented for
    ``transfer_number`` and ``order.py`` for ``order_number``. To get the
    spec's literal ``uq_shipment_number``, this model uses an **explicit**
    ``UniqueConstraint("shipment_number", name=uq_index_name("shipment",
    "number"))`` instead of column-level ``unique=True``, passing the helper
    a bare descriptor of ``"number"`` (not ``"shipment_number"``) so
    ``uq_index_name`` assembles ``uq_`` + ``shipment`` + ``number`` ->
    ``uq_shipment_number`` exactly, without the doubled segment. Flagged
    explicitly so a future edit doesn't "clean this up" back to column-level
    ``unique=True``, silently reintroducing the doubled name.

``state`` / ``shipping_payer`` -- ``VARCHAR(16)``, EXACT spec match via
``state_token_type()``:
    Unlike ``stock_transfer.state`` / ``order_line.fulfillment_mode`` (which
    both had to fall back to the placeholder ``state_token_long_type()`` ->
    ``VARCHAR(24)`` because no exact-width factory existed for their spec's
    ``VARCHAR(20)``), this table's two enum-like columns are spec'd at
    exactly ``VARCHAR(16)`` -- precisely ``state_token_type()``'s own width.
    No placeholder needed for either column. ``state`` additionally carries
    ``NOT NULL DEFAULT 'CREATED'``, mirroring ``stock_transfer.state`` /
    ``order.state``'s own dual ``default=`` / ``server_default=sa_text(...)``
    quoted-string-default pattern exactly.

``shipping_currency_id`` -- retrofitted to a real FK, though omitted from
the spec's compact §3 Foreign Keys bullet list:
    The spec's §3 "Foreign Keys" line only names ``order_id`` /
    ``source_warehouse_id`` / ``carrier_id`` / ``shipped_by`` -- but the §4
    Column Definitions table marks ``shipping_currency_id`` with description
    "FK" and the ``currency`` table already exists in this codebase
    (``database/models/currency.py``). This is treated as a spec
    compactness gap, not an intentional omission -- exactly the same
    "column definitions table says FK; foreign keys bullet list happens not
    to enumerate it" situation already resolved for other tables' currency
    columns (e.g. ``order.currency_id``, ``price_history.currency_id``).
    Given a real ``ForeignKey("currency.id")``, ``NOT NULL`` per spec.

``shipped_by`` -- nullable, distinct from UAC's own ``created_by`` /
``updated_by``:
    This table's own spec'd business actor ("who shipped this" -- set once
    the shipment is dispatched, hence nullable pre-dispatch), the same
    "business column vs. mixin audit column, same target table, different
    semantic role" situation already documented on
    ``stock_transfer.requested_by`` / ``order_status_history.actor_user_id``.

Column-type choices:

* ``shipment_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``, exact
  spec match (the same ``*_number`` business-document-key case
  ``business_key_type()``'s own docstring names by example, including
  "shipment").
* ``state`` / ``shipping_payer`` -- ``state_token_type()`` -> ``VARCHAR(16)``,
  EXACT spec match (see dedicated note above -- no placeholder needed).
* ``shipping_cost`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, exact spec
  match; mirrors ``order``/``order_line``'s own money-column dual
  ``default=0`` / ``server_default=sa_text("0")`` pattern.
* ``tracking_number`` -- ``tracking_number_type()`` -> ``VARCHAR(80)``, exact
  spec match (this is precisely the column that factory's own docstring
  names by example).
* ``shipped_at`` / ``delivered_at`` -- ``DateTime(timezone=True)``, nullable,
  no default -- each is set once, later, by the application as the shipment
  progresses (mirrors ``stock_transfer.approved_at`` /
  ``dispatched_at``/``received_at``'s own treatment).

Soft-delete -- added per spec, same qualified treatment as
``stock_transfer.py`` / ``order.py``:
    Spec §12: *"Supported, though a failed shipment should use state='FAILED'
    rather than deletion."* ``deleted_at`` is added, unconditionally nullable
    at the column level -- the "prefer FAILED" guidance is a
    service-layer/operational preference, not a schema-level restriction.

Naming convention:
    ``shipment_number``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("shipment", "number")``, NOT column-level
    ``unique=True``. All three CHECKs use ``ck_index_name`` normally: the
    standard helper output already matches the spec's three literal names
    verbatim (``ck_shipment_state``, ``ck_shipment_payer``,
    ``ck_shipment_cost_nonneg``) -- no override needed. Every FK uses
    ``fk_index_name`` normally. The three recommended single-column indexes
    use ``idx_index_name`` with no override needed. The partial index
    ``idx_shipment_active`` is produced by plain ``idx_index_name("shipment",
    "active")`` -- the helper's normal output already matches the spec's
    literal name verbatim.

Out of scope for this model (not implemented here):
    * The SALE_OUT / FACTORY_DIRECT_SHIPMENT ``inventory_transaction``
      posting at the DISPATCHED transition, and the DELIVERED ->
      ``order.state`` advancement -- both spec-flagged
      application-orchestrated, cross-table concerns.
    * Range partitioning by ``shipped_at`` (yearly) -- spec marks this
      optional/future, lower priority than the ledger tables.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional header), spec §13: *"Standard
    UAC; state transitions mirrored to shipment_status_history (T16)"*.
    ``Shipment`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every other
    UAC-using header table (``stock_transfer``, ``order``).
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
from database.naming import ck_index_name, fk_index_name, idx_index_name, uq_index_name
from database.types import business_key_type, money_type, state_token_type, tracking_number_type


class Shipment(Base, UniversalAuditColumns):
    """``T14 — shipment`` — physical shipment tied to an order (Classification: T)."""

    __tablename__ = "shipment"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------- shipment_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    shipment_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------ order_id
    order_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order.id",
            name=fk_index_name("shipment", "order_id", "order"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------- source_warehouse_id
    source_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("shipment", "source_warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------ carrier_id
    carrier_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "carrier.id",
            name=fk_index_name("shipment", "carrier_id", "carrier"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------ shipped_by
    # This table's own spec'd business actor -- distinct from UAC's mixin
    # created_by/updated_by. Nullable per spec. See module docstring's
    # dedicated section.
    shipped_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("shipment", "shipped_by", "app_user"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------------------ state
    # VARCHAR(16) -- EXACT spec match via state_token_type() (no placeholder
    # needed, unlike stock_transfer.state's own VARCHAR(20) situation).
    # Quoted-string default mirrors stock_transfer.state's own pattern.
    state: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
        default="CREATED",
        server_default=sa_text("'CREATED'"),
    )

    # -------------------------------------------------------------- shipped_at
    shipped_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------ delivered_at
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ---------------------------------------------------------- shipping_cost
    shipping_cost: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------- shipping_currency_id
    # Marked "FK" in the spec's Column Definitions table (though omitted
    # from the compact §3 Foreign Keys bullet list) -- given a real
    # ForeignKey to currency.id. See module docstring's dedicated section.
    shipping_currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("shipment", "shipping_currency_id", "currency"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ shipping_payer
    # VARCHAR(16) -- EXACT spec match via state_token_type().
    shipping_payer: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------- tracking_number
    tracking_number: Mapped[str | None] = mapped_column(
        tracking_number_type(),
        nullable=True,
    )

    # -------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete" section: spec §12 supports soft
    # delete, though FAILED state is the normal termination path.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "shipment_number") so the assembled
        # name is uq_shipment_number, not the doubled
        # uq_shipment_shipment_number that column-level unique=True's
        # implicit convention would produce.
        UniqueConstraint(
            "shipment_number",
            name=uq_index_name("shipment", "number"),
        ),
        # CHECK: full 7-value ShipmentState vocabulary, transcribed verbatim
        # from the spec. Reused verbatim by shipment_status_history.py's own
        # ck_shipment_status_history_states CHECK, to guarantee the two can
        # never silently drift apart.
        CheckConstraint(
            "state IN ("
            "'CREATED', 'PICKING', 'PACKED', 'DISPATCHED', 'IN_TRANSIT', "
            "'DELIVERED', 'FAILED'"
            ")",
            name=ck_index_name("shipment", "state"),
        ),
        # CHECK: ShippingPayer 3-value vocabulary.
        CheckConstraint(
            "shipping_payer IN ('CUSTOMER', 'REPRESENTATIVE', 'FACTORY')",
            name=ck_index_name("shipment", "payer"),
        ),
        # CHECK: shipping_cost non-negative.
        CheckConstraint(
            "shipping_cost >= 0",
            name=ck_index_name("shipment", "cost_nonneg"),
        ),
        # Recommended single-column indexes.
        Index(
            idx_index_name("shipment", "order_id"),
            "order_id",
        ),
        Index(
            idx_index_name("shipment", "carrier_id"),
            "carrier_id",
        ),
        Index(
            idx_index_name("shipment", "tracking_number"),
            "tracking_number",
        ),
        # Partial index -- active-shipment dashboard (not yet
        # DELIVERED/FAILED).
        Index(
            idx_index_name("shipment", "active"),
            "state",
            postgresql_where=sa_text("state NOT IN ('DELIVERED', 'FAILED')"),
        ),
    )


__all__ = ["Shipment"]
