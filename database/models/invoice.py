"""``T17 — invoice`` ORM model (invoice header generated from shipped/fulfilled orders).

Authority: ``07_DATABASE_SPEC.md`` §T17 — ``T17 — invoice`` **has** a full
detailed section in the physical spec, so the spec is primary authority here;
``06_ERD.md`` (F.6 — Finance / Invoicing, T17 line) is
secondary/corroborating only::

    T17 — invoice
    Purpose: Invoice header generated from one or more shipped/fulfilled
        orders (BR-F1, SRS E19).
    PK: id (UUID)
    FK: customer_id -> customer.id; currency_id -> currency.id; created_by
        -> app_user.id
    Column Definitions: +UAC; invoice_number VARCHAR(40) NOT NULL;
        customer_id UUID NOT NULL; currency_id UUID NOT NULL; state
        VARCHAR(20) NOT NULL DEFAULT 'DRAFT'; subtotal NUMERIC(18,4) NOT
        NULL DEFAULT 0; tax_total NUMERIC(18,4) NOT NULL DEFAULT 0;
        discount_total NUMERIC(18,4) NOT NULL DEFAULT 0; grand_total
        NUMERIC(18,4) NOT NULL DEFAULT 0; amount_paid NUMERIC(18,4) NOT NULL
        DEFAULT 0 (non-authoritative cache, reconciled from
        payment_allocation); balance_due NUMERIC(18,4) NOT NULL DEFAULT 0
        (grand_total - amount_paid, non-authoritative cache); issued_at
        TIMESTAMPTZ NULL; due_at TIMESTAMPTZ NULL; closed_at TIMESTAMPTZ
        NULL
    Unique: uq_invoice_number (invoice_number)
    Check: ck_invoice_state (state IN ('DRAFT','ISSUED','PARTIALLY_PAID',
        'PAID','CLOSED_CORRECTED','VOID')); ck_invoice_totals_nonneg
        (subtotal >= 0 AND tax_total >= 0 AND discount_total >= 0 AND
        grand_total >= 0)
    Business constraints: May aggregate lines from multiple orders, resolved
        via invoice_order (J1); once state IN ('PAID','CLOSED_CORRECTED')
        the header and its lines are immutable -- enforced via BEFORE
        UPDATE trigger; corrections are issued only via credit_note (T20),
        never a direct edit; amount_paid/balance_due are written only by
        the reconciliation job that sums payment_allocation, never by
        general application code -- enforced by column-level GRANT
        restricting UPDATE (amount_paid, balance_due) to the reconciliation
        service role.
    Recommended Indexes: btree on customer_id
    Composite Indexes: (customer_id, state) -- AR dashboard
    Partial Indexes: idx_invoice_ar_aging ON invoice (due_at) WHERE state IN
        ('ISSUED','PARTIALLY_PAID') -- the AR-aging report's hot path
    Partitioning Strategy: Optional -- range partition by issued_at
        (yearly) for archival once volume justifies it.
    Soft Delete Strategy: Supported pre-ISSUED only (a DRAFT invoice can be
        withdrawn); post-ISSUED invoices are never deleted, only corrected
        via credit_note or transitioned to VOID.
    Audit Strategy: Standard UAC; state transitions mirrored to
        invoice_history (H4); financial-sensitivity means all mutations are
        also visible in audit_log.

Not part of any of the three aggregate roots (``StockTransfer``,
``Shipment``, and this change's own ``OrderPriceFreeze``) touched by prior
work in this codebase. ``invoice`` is its own root: ``invoice_line`` (T18,
this same change) carries an ``invoice_id`` FK back to this table; the
future ``invoice_history`` (H4) and ``invoice_order`` (J1) junction (both
out of scope for this change -- see below) would do the same.

Non-reserved-word FK targets -- ``customer_id -> customer.id`` /
``currency_id -> currency.id``:
    Both are ordinary identifiers, no quoting concerns for either FK.

``created_by`` in the spec's §3 Foreign Keys bullet list is UAC's own mixin
column, not a distinct business column:
    Unlike ``shipment.shipped_by`` / ``stock_transfer.requested_by`` (each
    a table-specific business actor column distinct from the mixin's own
    ``created_by``), this table's §4 Column Definitions table lists **only**
    ``+UAC`` plus the columns below -- there is no separate ``created_by``
    (or similarly-named actor) column in the column list itself. The §3
    bullet's ``created_by -> app_user.id`` is simply calling out that UAC's
    own mixin-supplied ``created_by`` (already a real FK to ``app_user.id``
    per ``database/mixins.py``) is this table's posting actor -- no
    additional column is declared here. Flagged explicitly so a future edit
    doesn't add a redundant second ``created_by``-shaped column.

CRITICAL naming trap -- ``invoice_number``'s unique constraint:
    The spec's literal constraint name is ``uq_invoice_number``. Column-level
    ``unique=True`` on ``invoice_number`` would actually *also* produce this
    exact name here (``NAMING_CONVENTION["uq"]`` ->
    ``uq_invoice_invoice_number``... wait: table name ``invoice`` is NOT a
    prefix-sharing collision with column name ``invoice_number`` in the
    "doubled word" sense ``stock_transfer.transfer_number`` /
    ``shipment.shipment_number`` hit, because ``uq_%(table_name)s_%(column_0_name)s``
    would render ``uq_invoice_invoice_number`` (still doubled: "invoice"
    appears twice) -- NOT the spec's ``uq_invoice_number``. This *is* the
    same naming trap after all. This model therefore uses an **explicit**
    ``UniqueConstraint("invoice_number", name=uq_index_name("invoice",
    "number"))``, passing the helper a bare descriptor of ``"number"`` (not
    ``"invoice_number"``) so the assembled name is ``uq_invoice_number``
    exactly -- the same treatment ``stock_transfer.transfer_number`` /
    ``shipment.shipment_number`` already received. Flagged explicitly so a
    future edit doesn't "simplify" this to column-level ``unique=True``.

``state`` -- ``VARCHAR(20)`` per spec, placeholder width, NOT an exact match:
    Same situation as ``stock_transfer.state`` -- no ``database.types``
    factory produces exactly 20 characters (``state_token_type()`` -> 16,
    ``state_token_long_type()`` -> 24). ``state_token_long_type()`` is used
    as the closest existing factory. ``NOT NULL DEFAULT 'DRAFT'`` mirrors
    ``stock_transfer.state`` / ``order.state``'s own dual ``default=`` /
    ``server_default=sa_text(...)`` quoted-string-default pattern.

``amount_paid`` / ``balance_due`` -- non-authoritative caches, ordinary
``NOT NULL DEFAULT 0`` columns at the schema level:
    The spec's own business-constraints note that these are written
    *only* by a reconciliation job via column-level ``GRANT`` restriction --
    a database-permissions concern applied at deployment/migration time, not
    something the ORM column definition itself can express. Both columns
    are declared as ordinary ``money_type()`` columns with the spec's
    ``DEFAULT 0``; the write-restriction is flagged as out of scope for this
    model (see below), the same treatment ``customer_ledger.current_balance``
    already receives per the spec's own parallel note on that table.

Column-type choices:

* ``invoice_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``, exact
  spec match.
* ``state`` -- ``state_token_long_type()`` -> ``VARCHAR(24)``, placeholder
  for the spec's ``VARCHAR(20)`` (see dedicated note above).
* ``subtotal`` / ``tax_total`` / ``discount_total`` / ``grand_total`` /
  ``amount_paid`` / ``balance_due`` -- ``money_type()`` -> ``NUMERIC(18, 4)``,
  exact spec match; each carries the spec's ``DEFAULT 0`` via the dual
  ``default=0`` / ``server_default=sa_text("0")`` pattern this codebase's
  other money columns already use.
* ``issued_at`` / ``due_at`` / ``closed_at`` -- ``DateTime(timezone=True)``,
  nullable, no default -- each is set once, later, by the application as the
  invoice progresses through its state machine (mirrors
  ``stock_transfer.approved_at`` / ``dispatched_at`` / ``received_at``'s own
  treatment).

Soft-delete -- added per spec, qualified treatment:
    Spec §12: *"Supported pre-ISSUED only ... post-ISSUED invoices are never
    deleted, only corrected via credit_note or transitioned to VOID."*
    ``deleted_at`` is added, unconditionally nullable at the column level --
    the "pre-ISSUED only" restriction is a service-layer/application
    enforcement concern (the schema cannot conditionally forbid setting
    ``deleted_at`` based on ``state`` without a trigger, which the spec does
    not call for here), the same qualified-soft-delete treatment
    ``shipment.py`` / ``stock_transfer.py`` already receive for their own
    conditional soft-delete notes.

Naming convention:
    ``invoice_number``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("invoice", "number")``, NOT column-level
    ``unique=True``. Both CHECKs use ``ck_index_name`` normally: the
    standard helper output already matches the spec's two literal names
    verbatim (``ck_invoice_state``, ``ck_invoice_totals_nonneg``) -- no
    override needed. Both FKs use ``fk_index_name`` normally. The
    recommended single-column index and the composite ``(customer_id,
    state)`` index both use ``idx_index_name`` (the latter with
    ``composite_descriptor``) -- no literal override needed for either. The
    partial index ``idx_invoice_ar_aging`` is produced by plain
    ``idx_index_name("invoice", "ar_aging")`` -- the helper's normal output
    already matches the spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * ``invoice_history`` (H4) and ``invoice_order`` (J1) -- both are
      separate tables referencing this one, not part of this change's
      three-table scope (``order_price_freeze`` / ``invoice`` /
      ``invoice_line``).
    * The ``BEFORE UPDATE`` immutability trigger for
      ``state IN ('PAID','CLOSED_CORRECTED')`` -- a database-trigger-level
      concern, not an ORM column/constraint concern.
    * The column-level ``GRANT`` restricting ``UPDATE (amount_paid,
      balance_due)`` to the reconciliation service role -- a
      permissions/deployment-time concern, not expressible in the ORM
      model layer.
    * Range partitioning by ``issued_at`` (yearly) -- spec marks this
      optional/future.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional header), spec §13: *"Standard
    UAC; state transitions mirrored to invoice_history (H4)"*. ``Invoice``
    therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every other
    UAC-using header table (``shipment``, ``stock_transfer``, ``order``).
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
from database.naming import (
    ck_index_name,
    composite_descriptor,
    fk_index_name,
    idx_index_name,
    uq_index_name,
)
from database.types import business_key_type, money_type, state_token_long_type


class Invoice(Base, UniversalAuditColumns):
    """``T17 — invoice`` — invoice header generated from shipped/fulfilled orders (Classification: T)."""

    __tablename__ = "invoice"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------- invoice_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    invoice_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------ customer_id
    customer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("invoice", "customer_id", "customer"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------ currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("invoice", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------------ state
    # Placeholder width -- see module docstring's dedicated section.
    # Quoted-string default mirrors stock_transfer.state's own pattern.
    state: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
        default="DRAFT",
        server_default=sa_text("'DRAFT'"),
    )

    # ------------------------------------------------------------------- subtotal
    subtotal: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------------ tax_total
    tax_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # -------------------------------------------------------------- discount_total
    discount_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ---------------------------------------------------------------- grand_total
    grand_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------------ amount_paid
    # Non-authoritative cache -- see module docstring's dedicated section.
    amount_paid: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------------ balance_due
    # Non-authoritative cache -- see module docstring's dedicated section.
    balance_due: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # -------------------------------------------------------------------- issued_at
    issued_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ----------------------------------------------------------------------- due_at
    due_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------------------------------------------------------- closed_at
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete" section: spec §12 supports soft
    # delete pre-ISSUED only (application-enforced restriction).
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "invoice_number") so the assembled
        # name is uq_invoice_number, not the doubled
        # uq_invoice_invoice_number that column-level unique=True's
        # implicit convention would produce.
        UniqueConstraint(
            "invoice_number",
            name=uq_index_name("invoice", "number"),
        ),
        # CHECK: full 6-value InvoiceState vocabulary, transcribed verbatim
        # from the spec.
        CheckConstraint(
            "state IN ("
            "'DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'PAID', "
            "'CLOSED_CORRECTED', 'VOID'"
            ")",
            name=ck_index_name("invoice", "state"),
        ),
        # CHECK: the four resolved-total columns non-negative. amount_paid/
        # balance_due are deliberately NOT included -- the spec's own §6
        # names exactly these four columns, not the two reconciliation
        # caches.
        CheckConstraint(
            "subtotal >= 0 AND tax_total >= 0 AND discount_total >= 0 "
            "AND grand_total >= 0",
            name=ck_index_name("invoice", "totals_nonneg"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("invoice", "customer_id"),
            "customer_id",
        ),
        # Composite index -- (customer_id, state), AR dashboard.
        Index(
            idx_index_name("invoice", composite_descriptor(("customer_id", "state"))),
            "customer_id",
            "state",
        ),
        # Partial index -- AR-aging report hot path.
        Index(
            idx_index_name("invoice", "ar_aging"),
            "due_at",
            postgresql_where=sa_text("state IN ('ISSUED', 'PARTIALLY_PAID')"),
        ),
    )


__all__ = ["Invoice"]
