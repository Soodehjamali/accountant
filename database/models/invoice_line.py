"""``T18 — invoice_line`` ORM model (invoice line items; price frozen at issue time).

Authority: ``07_DATABASE_SPEC.md`` §T18 — ``T18 — invoice_line`` **has** a
full detailed section in the physical spec, so the spec is primary authority
here; ``06_ERD.md`` (F.6 — Finance / Invoicing, T18 line) is
secondary/corroborating only::

    T18 — invoice_line
    Purpose: Line items of an invoice; price frozen at issue time (BR-P3,
        SRS E20).
    PK: id (UUID)
    FK: invoice_id -> invoice.id; order_line_id -> order_line.id
        (nullable); product_id -> product.id (nullable)
    Column Definitions: +UAC; invoice_id UUID NOT NULL; order_line_id UUID
        NULL (nullable for freight/manual lines); product_id UUID NULL
        (nullable for non-product lines); description VARCHAR(255) NOT
        NULL; qty NUMERIC(18,4) NOT NULL; unit_price NUMERIC(18,4) NOT NULL
        (frozen at issue); tax_rate NUMERIC(7,4) NOT NULL DEFAULT 0
        (percentage); tax_amount NUMERIC(18,4) NOT NULL DEFAULT 0;
        discount_value NUMERIC(18,4) NOT NULL DEFAULT 0; line_total
        NUMERIC(18,4) NOT NULL (application-computed)
    Unique: uq_invoice_line_order_line (invoice_id, order_line_id) WHERE
        order_line_id IS NOT NULL
    Check: ck_invoice_line_qty_positive (qty > 0);
        ck_invoice_line_unit_price_nonneg (unit_price >= 0);
        ck_invoice_line_tax_rate_range (tax_rate BETWEEN 0 AND 100)
    Business constraints: unit_price copied from order_line.unit_price at
        issue time, never re-resolved against price_history; immutable once
        the parent invoice's state <> 'DRAFT' -- enforced via BEFORE UPDATE
        trigger checking parent state.
    Recommended Indexes: btree on order_line_id; btree on product_id
    Composite Indexes: none beyond partial unique
    Partial Indexes: see Unique Constraints
    Partitioning Strategy: Follows invoice's partitioning if adopted
    Soft Delete Strategy: Supported pre-issue only
    Audit Strategy: Standard UAC

Owned by ``invoice`` (T17, this same change) via ``invoice_id``, the same
aggregate ``order_price_freeze.py`` (T13) is *not* part of (that table's own
parent is ``order_line``, not ``invoice``).

Non-reserved-word FK targets -- ``invoice_id -> invoice.id`` /
``order_line_id -> order_line.id`` / ``product_id -> product.id``:
    All three are ordinary identifiers, no quoting concerns for any FK.

Both nullable FKs, for distinct reasons per spec:
    ``order_line_id`` is nullable *"for freight/manual lines"* (an invoice
    line that doesn't correspond to any specific order line -- e.g. a
    shipping-fee line item); ``product_id`` is nullable *"for non-product
    lines"* (e.g. a service fee or adjustment line with no product
    identity). These are two independently-nullable columns for two
    different manual-line scenarios, not a single "both or neither" pair --
    no ``CheckConstraint`` ties them together, matching the spec's silence
    on any such coupling.

CRITICAL naming trap -- the unique constraint is a **partial** unique index,
not a plain ``UniqueConstraint``:
    The spec's constraint is ``uq_invoice_line_order_line (invoice_id,
    order_line_id) WHERE order_line_id IS NOT NULL`` -- a conditional
    (partial) uniqueness rule, since ``order_line_id`` is nullable and
    multiple freight/manual lines (each with ``order_line_id IS NULL``) must
    be allowed to coexist on one invoice without colliding. SQLAlchemy's
    ``UniqueConstraint`` has no ``WHERE`` clause support at all -- a partial
    unique constraint can only be expressed as a partial **unique index**:
    ``Index(..., unique=True, postgresql_where=...)``, the same mechanism
    the spec's own ``physical_count.uq_physical_count_one_open`` /
    ``approval_request.uq_approval_request_one_pending`` partial-unique
    rules already use elsewhere in this schema (per those tables' own spec
    sections), even though the *literal* name still carries a ``uq_``
    prefix by long-standing spec convention for one-of-a-kind partial
    uniqueness rules, not the ``idx_`` prefix an ordinary index would get.
    Passed the **literal** string ``"uq_invoice_line_order_line"`` directly
    (neither ``uq_index_name`` nor ``idx_index_name`` produces this shape on
    its own, since neither helper has a "partial unique index with a uq_
    prefix" mode) -- the same bare-literal-override treatment
    ``shipment_line.py``'s own ``uq_shipment_line`` naming trap already
    established for a different kind of naming-convention exception.

``description`` -- ``description_type()``, exact spec match:
    ``VARCHAR(255)`` per spec -- ``database.types.description_type()``'s own
    docstring names invoice-line-adjacent description columns
    (``invoice_line.description``, ``credit_note_line.description``)
    explicitly as its intended consumers.

``tax_rate`` -- ``rate_type()``, exact spec match:
    ``NUMERIC(7, 4)`` per spec -- ``database.types.rate_type()``'s own
    docstring names percentage-rate columns like this one as its intended
    use case; the ``BETWEEN 0 AND 100`` bound is expressed as a CHECK below
    (``ck_invoice_line_tax_rate_range``), not encoded in the type itself, per
    ``rate_type()``'s own documented contract.

``unit_price`` -- frozen at issue, application-orchestrated, not
schema-enforced:
    Spec §7: *"unit_price copied from order_line.unit_price at issue time,
    never re-resolved against price_history"* -- a service-layer copy
    operation at invoice-issue time, not something a schema-level default
    or trigger performs here (no default is declared; the application
    always supplies it).

``line_total`` -- application-computed, no default, same treatment as
``order_line.line_total`` / ``transfer_line.qty_variance``'s sibling
columns:
    Spec gives no ``DEFAULT`` for this column (unlike ``tax_amount`` /
    ``discount_value``, which do have ``DEFAULT 0``) -- the application
    always computes and supplies it at write time, mirroring
    ``order_line.line_total``'s own no-default, application-computed
    treatment.

Column-type choices:

* ``description`` -- ``description_type()`` -> ``VARCHAR(255)``, exact spec
  match (see dedicated note above).
* ``qty`` / ``unit_price`` / ``tax_amount`` / ``discount_value`` /
  ``line_total`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, exact spec
  match. ``tax_amount`` / ``discount_value`` carry the spec's ``DEFAULT 0``
  via the dual ``default=0`` / ``server_default=sa_text("0")`` pattern this
  codebase's other money columns already use; ``qty`` / ``unit_price`` /
  ``line_total`` have no default (application always supplies them).
* ``tax_rate`` -- ``rate_type()`` -> ``NUMERIC(7, 4)``, exact spec match
  (see dedicated note above), ``DEFAULT 0``.

Soft-delete -- added per spec, qualified treatment:
    Spec §12: *"Supported pre-issue only"* -- the same
    application-enforced-restriction treatment ``invoice.py``'s own
    "pre-ISSUED only" soft-delete note already receives; ``deleted_at`` is
    added, unconditionally nullable at the column level.

Naming convention:
    The unique constraint is the partial-unique-index naming-trap case
    explained above -- the bare literal ``"uq_invoice_line_order_line"``
    passed directly to ``Index(..., unique=True, postgresql_where=...)``,
    NOT ``UniqueConstraint`` (which cannot express a ``WHERE`` clause at
    all) and NOT ``idx_index_name``/``uq_index_name`` (neither helper
    produces this exact shape). All three CHECKs use ``ck_index_name``
    normally: the standard helper output already matches the spec's three
    literal names verbatim (``ck_invoice_line_qty_positive``,
    ``ck_invoice_line_unit_price_nonneg``,
    ``ck_invoice_line_tax_rate_range``) -- no override needed. Every FK uses
    ``fk_index_name`` normally. Both recommended single-column indexes
    (``order_line_id``, ``product_id``) use ``idx_index_name`` with no
    override needed.

Out of scope for this model (not implemented here):
    * The ``BEFORE UPDATE`` immutability trigger for "parent invoice's
      state <> 'DRAFT'" -- a database-trigger-level, cross-table concern,
      not an ORM column/constraint concern.
    * Partitioning -- spec marks this "Follows invoice's partitioning if
      adopted", i.e. no partitioning is declared here directly.
    * Any Alembic migration.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Classification ``T`` (mutable transactional line item), spec §13:
    *"Standard UAC"*. ``InvoiceLine`` therefore gets the full
    ``created_at``/``updated_at``/``created_by``/``updated_by``/``version``
    set and opts its ``version`` column into optimistic locking via
    ``__mapper_args__ = {"version_id_col": "version"}``, same as every other
    UAC-using line-item table (``order_line``, ``transfer_line``,
    ``shipment_line``).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import description_type, money_type, rate_type


class InvoiceLine(Base, UniversalAuditColumns):
    """``T18 — invoice_line`` — invoice line items, price frozen at issue time (Classification: T)."""

    __tablename__ = "invoice_line"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- invoice_id
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "invoice.id",
            name=fk_index_name("invoice_line", "invoice_id", "invoice"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------- order_line_id
    # Nullable "for freight/manual lines" per spec. See module docstring's
    # dedicated section.
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order_line.id",
            name=fk_index_name("invoice_line", "order_line_id", "order_line"),
        ),
        nullable=True,
    )

    # --------------------------------------------------------------- product_id
    # Nullable "for non-product lines" per spec. See module docstring's
    # dedicated section.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("invoice_line", "product_id", "product"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- description
    description: Mapped[str] = mapped_column(
        description_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------------ qty
    # No spec default -- application always supplies it.
    qty: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- unit_price
    # Frozen at issue, copied from order_line.unit_price -- no default. See
    # module docstring's dedicated section.
    unit_price: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ---------------------------------------------------------------------- tax_rate
    tax_rate: Mapped[decimal.Decimal] = mapped_column(
        rate_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # -------------------------------------------------------------------- tax_amount
    tax_amount: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ----------------------------------------------------------------- discount_value
    discount_value: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # -------------------------------------------------------------------- line_total
    # Application-computed, no default. See module docstring's dedicated
    # section.
    line_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete" section: spec §12 supports soft
    # delete pre-issue only (application-enforced restriction).
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # PARTIAL UNIQUE INDEX -- see module docstring's "CRITICAL naming
        # trap" section. Cannot be a UniqueConstraint (no WHERE support);
        # bare literal name, matching the spec's own uq_-prefixed partial
        # unique index convention.
        Index(
            "uq_invoice_line_order_line",
            "invoice_id",
            "order_line_id",
            unique=True,
            postgresql_where=sa_text("order_line_id IS NOT NULL"),
        ),
        # CHECK: qty strictly positive.
        CheckConstraint(
            "qty > 0",
            name=ck_index_name("invoice_line", "qty_positive"),
        ),
        # CHECK: unit_price non-negative.
        CheckConstraint(
            "unit_price >= 0",
            name=ck_index_name("invoice_line", "unit_price_nonneg"),
        ),
        # CHECK: tax_rate within a valid percentage range.
        CheckConstraint(
            "tax_rate BETWEEN 0 AND 100",
            name=ck_index_name("invoice_line", "tax_rate_range"),
        ),
        # Recommended single-column indexes.
        Index(
            idx_index_name("invoice_line", "order_line_id"),
            "order_line_id",
        ),
        Index(
            idx_index_name("invoice_line", "product_id"),
            "product_id",
        ),
    )


__all__ = ["InvoiceLine"]
