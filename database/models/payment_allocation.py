"""``T20 (ERD: J2) — payment_allocation`` ORM model (payment-to-invoice allocation junction).

Authority: ``07_DATABASE_SPEC.md`` §J2 (spec's own section header:
``J2 — payment_allocation (Junction)``; this task's own T20 label refers to
the same table by its position in the requested build order, not a
distinct ERD code -- the ERD/spec numbering for this table is ``J2``) --
this table **does** have a full detailed spec section, so the spec is
primary authority here; ``06_ERD.md`` (F.6 — Finance / Invoicing, J2 line)
is secondary/corroborating only::

    J2 — payment_allocation (Junction)
    Purpose: Resolves N:N between payment and invoice (partial/split
        payments).
    PK: id (UUID)
    FK: payment_id -> payment.id; invoice_id -> invoice.id
    Column Definitions: id UUID NOT NULL DEFAULT gen_random_uuid()
        (surrogate PK -- this junction carries enough business-meaningful
        data to warrant a surrogate key rather than a composite PK);
        payment_id UUID NOT NULL; invoice_id UUID NOT NULL; allocated_amount
        NUMERIC(18,4) NOT NULL; allocated_at TIMESTAMPTZ NOT NULL DEFAULT
        now(); allocated_by UUID NOT NULL (FK -> app_user.id)
    Unique: uq_payment_allocation (payment_id, invoice_id)
    Check: ck_payment_allocation_amount_positive (allocated_amount > 0)
    Business constraints: Sum(allocated_amount) per payment_id <=
        payment.amount -- enforced via BEFORE INSERT/UPDATE trigger
        (cross-row aggregate, not expressible as CHECK); Sum(allocated_amount)
        per invoice_id <= invoice.grand_total -- same trigger pattern; once
        written, an allocation row is not edited -- corrections are a new
        negative-equivalent compensating allocation row plus a new positive
        one, keeping the table append-only in practice even though it is
        not formally classified H (this is a RECOMMENDATION, not an ERD
        mandate, flagged since the ERD classifies this table J without
        specifying immutability -- noted for product-owner confirmation).
    Recommended Indexes: btree on invoice_id; btree on payment_id
    Composite Indexes: none beyond unique constraint (both directions are
        covered by the unique index and its reverse btree)
    Partial Indexes: none
    Partitioning Strategy: None initially; consider alongside
        payment/invoice if those are partitioned.
    Soft Delete Strategy: None recommended (see Business Constraints) --
        corrections via compensating rows
    Audit Strategy: allocated_by captures the actor; all allocation events
        are financially sensitive and should also flow to audit_log
    Notes: Given its financial-reconciliation role, this table is a strong
        candidate to be reclassified as append-only (H-pattern) at
        implementation time even though the ERD lists it as J -- flagged as
        a recommendation for the ERD owner's future consideration, not
        applied unilaterally since the ERD is approved and unmodified.

AUDIT-MIXIN DECISION -- explicitly required by this task, documented in
full here: **NEITHER ``UniversalAuditColumns`` NOR ``AppendOnlyAuditColumns``
is applied to this model.**

    The decision process, reasoned from the spec text above:

    1. **The spec's own §4 Column Definitions table carries no ``+UAC`` /
       ``+AAC`` marker row at all.** Contrast this directly with every
       other table built in this codebase so far -- ``shipment`` (+UAC),
       ``payment`` itself (+AAC), ``order_price_freeze`` (+AAC),
       ``invoice_line`` (+UAC) -- each of which opens its column list with
       an explicit mixin marker. ``payment_allocation``'s column list
       instead starts directly with ``id UUID NOT NULL DEFAULT
       gen_random_uuid() -- Surrogate PK``, i.e. bespoke, fully-enumerated
       columns with no mixin shorthand at all. This is the same
       column-list shape ``invoice_order`` (J1, the sibling junction table
       one section earlier in the spec) already has -- J1's own column list
       is likewise bare (``invoice_id``, ``order_id``, ``linked_at``), no
       mixin marker.

    2. **The spec's own §7 Business Constraints text is explicit that
       formal reclassification to H (append-only) was deliberately NOT
       made:** *"keeping the table append-only in practice even though it
       is **not formally classified H** ... this is a **RECOMMENDATION**,
       not an ERD mandate ... noted for product-owner confirmation"*, and
       again in §15 Notes: *"a strong candidate to be reclassified ... **not
       applied unilaterally since the ERD is approved and unmodified**."*
       Applying ``AppendOnlyAuditColumns`` here would silently perform
       exactly the reclassification the spec itself declines to make
       pending product-owner confirmation -- the ORM layer is not the place
       to unilaterally resolve an open ERD-governance question the spec's
       own authors have explicitly deferred.

    3. **The column set doesn't cleanly match either mixin's shape even in
       spirit.** UAC would add ``created_at``/``updated_at``/``created_by``/
       ``updated_by``/``version`` -- but this table already has its own
       spec'd ``allocated_at`` (playing ``created_at``'s role) and
       ``allocated_by`` (playing ``created_by``'s role) as **named business
       columns**, and the spec gives no ``updated_at``/``updated_by``/
       ``version`` at all (consistent with "not edited" per Business
       Constraints). AAC would add ``created_at``/``created_by`` only --
       structurally closer, but adopting it would still introduce a
       generic ``created_at``/``created_by`` pair that duplicates
       ``allocated_at``/``allocated_by``'s own semantic role under
       different, mixin-supplied names, the same
       "redundant near-duplicate pair" concern
       ``shipment.shipped_by``-vs-AAC's-``created_by`` deliberately avoids
       elsewhere by keeping the business column and letting the *mixin*
       supply the generic pair alongside it -- but here there IS no mixin
       to supply a second, generic pair, because the spec itself supplies
       none.

    **Conclusion:** this model declares the spec's exact, literal column
    list directly on ``Base`` (no mixin), mirroring the ``invoice_order``
    (J1) junction-table precedent this same spec establishes one section
    earlier for a structurally identical situation (bare column list, no
    audit-mixin marker, surrogate-vs-composite PK choice made explicitly in
    prose). If the ERD owner later confirms the H-reclassification
    recommendation from §15 Notes, that would be a **future, explicit**
    migration (adding ``AppendOnlyAuditColumns`` and dropping the now
    redundant ``allocated_at``/``allocated_by``, or renaming them to
    ``created_at``/``created_by``) -- not something this model silently
    anticipates.

Not part of any of the three aggregate roots (``StockTransfer``,
``Shipment``, ``Invoice``) touched by prior changes in this codebase; like
``invoice_order`` (J1), this is a pure resolving junction between two
existing aggregate roots (``payment``, this same change, and ``invoice``,
already built), not owned by either as a line-item child.

Non-reserved-word FK targets -- ``payment_id -> payment.id`` /
``invoice_id -> invoice.id`` / ``allocated_by -> app_user.id``:
    All three are ordinary identifiers, no quoting concerns for any FK.

Surrogate PK, not composite -- explicit per spec:
    Spec §4's own inline note: *"Surrogate PK (this junction carries enough
    business-meaningful data to warrant a surrogate key rather than a
    composite PK)"* -- unlike ``invoice_order`` (J1), whose own §2 states
    *"Primary Key: composite (invoice_id, order_id)"*. This model therefore
    uses the standard ``id: GuidPk = id_column()`` surrogate-PK pattern
    every other table in this codebase uses, NOT a composite
    ``PrimaryKeyConstraint(payment_id, invoice_id)``.

CRITICAL naming trap -- the unique constraint has NO descriptor suffix at
all, just the bare table name:
    The spec's literal constraint name is ``uq_payment_allocation`` --
    notably **without** a trailing column-derived descriptor the way every
    other unique constraint in this codebase has one (contrast
    ``uq_shipment_number``, ``uq_transfer_line``, ``uq_invoice_line_order_line``,
    all of which append a suffix beyond the bare table name). Column-level
    ``unique=True`` is not applicable here regardless (this is a 2-column
    composite unique, not a single-column one), and running
    ``composite_descriptor(("payment_id", "invoice_id"))`` through
    ``uq_index_name`` would produce
    ``uq_payment_allocation_payment_id_invoice_id`` -- far longer than the
    spec's own bare ``uq_payment_allocation``. This model therefore passes
    the **literal** string ``name="uq_payment_allocation"`` directly to
    ``UniqueConstraint``, the same bare-literal-override treatment
    ``shipment_line.py``'s ``uq_shipment_line`` /
    ``invoice_line.py``'s ``uq_invoice_line_order_line`` naming traps
    already established, extended here to the edge case where the literal
    name has *no* descriptor suffix whatsoever beyond ``uq_`` + the table
    name.

``allocated_amount`` -- ``money_type()``, exact spec match, no default:
    ``NUMERIC(18, 4)`` per spec, ``NOT NULL``, no ``DEFAULT`` -- the
    application always supplies a specific allocation amount at write time.

``allocated_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment every other ``*_at`` posting
    column in this codebase receives (``received_at`` on ``payment``,
    ``requested_at`` on ``stock_transfer``).

``allocated_by`` -- the sole actor column on this table (see the mixin
decision above for why it is NOT paired with a mixin-supplied
``created_by``):
    Real ``ForeignKey("app_user.id")``, ``NOT NULL`` per spec. Spec §13:
    *"allocated_by captures the actor"* -- this is the table's entire audit
    story, by explicit spec design (no separate mixin-supplied actor
    column exists here, unlike ``payment.received_by`` which sits alongside
    AAC's own ``created_by``).

Column-type choices:

* ``allocated_amount`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, exact spec
  match, no default (see dedicated note above).
* ``allocated_at`` -- ``DateTime(timezone=True)``, ``NOT NULL DEFAULT
  now()``.

No ``deleted_at`` -- explicit per spec:
    Spec §12: *"None recommended (see Business Constraints) -- corrections
    via compensating rows"* -- the same unconditional-absence treatment
    ``payment.py`` / ``transfer_history.py`` already receive for their own
    append-only-in-practice Soft Delete Strategy notes.

No ``__mapper_args__`` -- no ``version`` column exists on this table (no
mixin supplies one, and the spec's own column list has none), so there is
no optimistic-lock token to opt into.

Naming convention:
    The unique constraint is the "bare table name, no descriptor suffix"
    naming-trap case explained above -- the literal
    ``name="uq_payment_allocation"``, NOT
    ``uq_index_name(table, composite_descriptor(...))``. The CHECK uses
    ``ck_index_name`` normally: the standard helper output already matches
    the spec's literal name verbatim
    (``ck_payment_allocation_amount_positive``) -- no override needed.
    Both FKs use ``fk_index_name`` normally. Both recommended
    single-column indexes (``invoice_id``, ``payment_id``) use
    ``idx_index_name`` with no override needed -- declared as two separate
    ``Index`` objects per the spec's own explicit "btree on invoice_id;
    btree on payment_id" wording, even though the unique constraint's own
    supporting index already partially covers ``payment_id``-first lookups
    (the spec calls out both directions explicitly, so both are built
    here rather than silently dropping the seemingly-redundant one).

Out of scope for this model (not implemented here):
    * The ``BEFORE INSERT/UPDATE`` triggers enforcing
      ``SUM(allocated_amount) <= payment.amount`` /
      ``SUM(allocated_amount) <= invoice.grand_total`` -- both explicitly
      spec-flagged as cross-row aggregate, trigger-level concerns, not
      expressible as a ``CheckConstraint``.
    * The compensating-row correction pattern for edits -- an
      application/ledger-layer concern, the same treatment
      ``inventory_transaction``'s own reversal pattern receives elsewhere.
    * Flowing allocation events to ``audit_log`` -- an
      application-orchestrated, cross-table concern (spec §13's own
      "should also flow to audit_log" is advisory, not a schema-level FK
      or trigger this model adds).
    * Any Alembic migration.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import money_type


class PaymentAllocation(Base):
    """``T20 (ERD: J2) — payment_allocation`` — payment-to-invoice allocation junction (Classification: J, no audit mixin -- see module docstring)."""

    __tablename__ = "payment_allocation"

    # ------------------------------------------------------------------ id
    # Surrogate PK, explicit per spec -- see module docstring's dedicated
    # section (NOT a composite PK, unlike invoice_order/J1).
    id: GuidPk = id_column()

    # -------------------------------------------------------------- payment_id
    payment_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "payment.id",
            name=fk_index_name("payment_allocation", "payment_id", "payment"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- invoice_id
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "invoice.id",
            name=fk_index_name("payment_allocation", "invoice_id", "invoice"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------- allocated_amount
    # No spec default -- application always supplies it.
    allocated_amount: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- allocated_at
    allocated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ------------------------------------------------------------- allocated_by
    # The table's sole actor column -- see module docstring's audit-mixin
    # decision section for why no mixin-supplied created_by sits alongside
    # it.
    allocated_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("payment_allocation", "allocated_by", "app_user"),
        ),
        nullable=False,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Bare literal name (no descriptor suffix at all), NOT
        # composite_descriptor + uq_index_name.
        UniqueConstraint(
            "payment_id",
            "invoice_id",
            name="uq_payment_allocation",
        ),
        # CHECK: allocated_amount strictly positive.
        CheckConstraint(
            "allocated_amount > 0",
            name=ck_index_name("payment_allocation", "amount_positive"),
        ),
        # Recommended single-column indexes -- both directions, per spec's
        # own explicit wording. See module docstring's "Naming convention"
        # section for why both are kept despite partial overlap with the
        # unique constraint's own supporting index.
        Index(
            idx_index_name("payment_allocation", "invoice_id"),
            "invoice_id",
        ),
        Index(
            idx_index_name("payment_allocation", "payment_id"),
            "payment_id",
        ),
    )


__all__ = ["PaymentAllocation"]
