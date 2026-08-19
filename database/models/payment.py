"""``T19 — payment`` ORM model (money received from a customer, independent of any single invoice).

Authority: ``07_DATABASE_SPEC.md`` §T19 — ``T19 — payment`` **has** a full
detailed section in the physical spec, so the spec is primary authority here;
``06_ERD.md`` (F.6 — Finance / Invoicing, T19 line) is
secondary/corroborating only::

    T19 — payment
    Purpose: Money received from a customer, recorded independently of any
        single invoice (SRS E21).
    PK: id (UUID)
    FK: customer_id -> customer.id; currency_id -> currency.id; received_by
        -> app_user.id
    Column Definitions: +AAC (payment is itself an event-sourced ledger --
        see Business Constraints); payment_number VARCHAR(40) NOT NULL;
        customer_id UUID NOT NULL; currency_id UUID NOT NULL; received_by
        UUID NOT NULL; amount NUMERIC(18,4) NOT NULL; method VARCHAR(20)
        NOT NULL (CASH|BANK_TRANSFER|CHEQUE|CARD|MOBILE_WALLET); reference
        VARCHAR(120) NULL (bank ref / cheque no.); received_at TIMESTAMPTZ
        NOT NULL DEFAULT now(); unallocated_amount NUMERIC(18,4) NOT NULL
        (non-authoritative cache, reconciled from payment_allocation)
    Unique: uq_payment_number (payment_number)
    Check: ck_payment_method (method IN ('CASH','BANK_TRANSFER','CHEQUE',
        'CARD','MOBILE_WALLET')); ck_payment_amount_positive (amount > 0);
        ck_payment_unallocated_range (unallocated_amount BETWEEN 0 AND
        amount)
    Business constraints: Immutable once posted -- no general UPDATE grant
        (same append-only posture as inventory_transaction); corrections
        via a compensating reversal payment (a new row with
        negative-equivalent effect handled at the application/ledger level);
        unallocated_amount is written only by the reconciliation job that
        sums payment_allocation, via column-level GRANT, identical pattern
        to invoice.amount_paid.
    Recommended Indexes: btree on customer_id
    Composite Indexes: none beyond above
    Partial Indexes: idx_payment_unallocated ON payment (customer_id) WHERE
        unallocated_amount > 0 -- "apply this payment" workflow
    Partitioning Strategy: Optional -- range partition by received_at
        (yearly) for archival.
    Soft Delete Strategy: None -- append-only per Business Constraints; a
        mis-posted payment is corrected via a compensating reversal
        payment, not deletion.
    Audit Strategy: created_by (AAC) is the posting actor; column-level
        GRANT permits only the reconciliation role to update
        unallocated_amount.
    Notes: Because this table is append-only (per ERD's explicit "immutable
        once posted"), it uses AAC, not the full UAC -- this is a
        deliberate deviation from "every T-classified table gets UAC",
        documented explicitly here since T19 is one of the ledger-pattern
        transactional tables (see ERD PART H) rather than an ordinary
        mutable transactional table.

Audit-mixin decision -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC, per the
spec's own explicit instruction:
    Unlike every other T-classified table touched by prior work in this
    codebase (``shipment``, ``stock_transfer``, ``invoice`` -- all spec'd
    ``+UAC``), this table's own §4 Column Definitions table opens with
    ``+AAC``, and the spec's own §15 Notes states the deviation in so many
    words: *"Because this table is append-only (per ERD's explicit
    'immutable once posted'), it uses AAC, not the full UAC -- this is a
    deliberate deviation from 'every T-classified table gets UAC'"*. This
    model follows that explicit instruction directly -- no independent
    judgment call was needed here, unlike ``payment_allocation.py``'s own
    mixin decision (see that module's docstring), where the spec gives no
    such explicit ``+UAC``/``+AAC`` marker at all.

Root of its own lineage, not owned by any prior aggregate:
    ``payment`` is not part of the ``StockTransfer`` / ``Shipment`` /
    ``Invoice`` aggregates touched by prior changes in this codebase; the
    ``payment_allocation`` junction (T20/J2, this same change) carries a
    ``payment_id`` FK to this table, resolving payment-to-invoice
    many-to-many splits without ``payment`` itself owning
    ``payment_allocation`` as a child in the aggregate-ownership sense (a
    junction table, not a line-item child).

Non-reserved-word FK targets -- ``customer_id -> customer.id`` /
``currency_id -> currency.id`` / ``received_by -> app_user.id``:
    All three are ordinary identifiers, no quoting concerns for any FK.

``received_by`` -- distinct business actor, NOT a redundant pair with AAC's
own ``created_by``:
    This table's own spec'd business actor ("who physically received this
    payment"), listed as its own column in the spec's §4 table (unlike
    ``invoice.py``'s situation, where the §3 FK bullet's ``created_by`` was
    simply naming UAC's own mixin column with no separate business column
    to declare). This mirrors the same "business column vs. mixin audit
    column, same target table, different semantic role" situation
    ``shipment.shipped_by`` / ``stock_transfer.requested_by`` already
    document -- except here the mixin is AAC, so the pair is
    ``received_by`` (business) vs. AAC's own ``created_by`` (generic
    append-only audit actor), both real FKs to ``app_user.id``, both
    typically the same person in practice but semantically distinct
    columns. ``NOT NULL`` per spec (every payment has a known receiver).

CRITICAL naming trap -- ``payment_number``'s unique constraint:
    The spec's literal constraint name is ``uq_payment_number``. Column-level
    ``unique=True`` on ``payment_number`` would NOT produce that name:
    ``NAMING_CONVENTION["uq"]`` is ``uq_%(table_name)s_%(column_0_name)s``, so
    the implicit path would render ``uq_payment_payment_number`` (table name
    + column name, both containing "payment", concatenated in full) --
    doubling the word rather than collapsing it. The exact same latent
    collision ``stock_transfer.py`` / ``shipment.py`` / ``invoice.py``
    already documented for their own ``*_number`` business keys. This model
    uses an **explicit** ``UniqueConstraint("payment_number",
    name=uq_index_name("payment", "number"))``, passing the helper a bare
    descriptor of ``"number"`` (not ``"payment_number"``) so the assembled
    name is ``uq_payment_number`` exactly.

``method`` -- ``VARCHAR(20)`` per spec, placeholder width, NOT an exact
match:
    Same situation as ``invoice.state`` / ``stock_transfer.state`` -- no
    ``database.types`` factory produces exactly 20 characters
    (``state_token_type()`` -> 16, ``state_token_long_type()`` -> 24).
    ``state_token_long_type()`` is used as the closest existing factory. No
    default is declared -- unlike ``state``-shaped enum columns elsewhere,
    the spec gives ``method`` no ``DEFAULT`` value (every payment must
    explicitly state how it was received).

``reference`` -- ``token_type()``, exact spec match AND semantic match:
    ``VARCHAR(120)`` per spec -- ``database.types.token_type()``'s own
    docstring explicitly names *"bank references"* among its intended
    consumers, which is precisely this column's own spec description
    ("Bank ref / cheque no."). A direct, documented fit rather than a
    placeholder choice. Nullable per spec (not every payment method has a
    reference -- e.g. ``CASH``).

``amount`` -- no default, application always supplies it (mirrors
``order_line.qty_ordered`` / ``transfer_line.qty_requested``'s own
no-default treatment):
    Every payment is created with a specific, application-supplied amount;
    the spec gives no ``DEFAULT`` for this column.

``unallocated_amount`` -- non-authoritative cache, ordinary schema-level
column, no default:
    The spec's own business-constraints note that this column is written
    *only* by a reconciliation job via column-level ``GRANT`` restriction --
    a database-permissions concern applied at deployment/migration time, not
    something the ORM column definition itself can express (the same
    treatment ``invoice.amount_paid`` / ``invoice.balance_due`` already
    receive). No default is declared -- the spec's own column table shows no
    ``DEFAULT`` value; the application sets it (typically equal to
    ``amount``) at insert time.

Column-type choices:

* ``payment_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``, exact
  spec match.
* ``method`` -- ``state_token_long_type()`` -> ``VARCHAR(24)``, placeholder
  for the spec's ``VARCHAR(20)`` (see dedicated note above).
* ``reference`` -- ``token_type()`` -> ``VARCHAR(120)``, exact spec match
  AND the factory's own documented intended use (see dedicated note above).
* ``amount`` / ``unallocated_amount`` -- ``money_type()`` -> ``NUMERIC(18,
  4)``, exact spec match; neither carries a default (see dedicated notes
  above).
* ``received_at`` -- ``DateTime(timezone=True)``, ``NOT NULL DEFAULT
  now()``, mirroring every other ``*_at`` posting-timestamp column in this
  codebase (``order.ordered_at``, ``stock_transfer.requested_at``).

No ``deleted_at`` -- explicit per spec:
    Spec §12: *"None -- append-only per Business Constraints; a mis-posted
    payment is corrected via a compensating reversal payment, not
    deletion."* Unlike ``shipment`` / ``stock_transfer`` / ``invoice`` (all
    qualified-"Supported"), this table has no soft-delete column at all --
    the same unconditional-absence treatment ``transfer_history`` /
    ``shipment_status_history`` already receive for their own append-only
    Soft Delete Strategy notes.

No ``__mapper_args__ = {"version_id_col": ...}``:
    AAC carries no ``version`` column (unlike UAC), so there is no
    optimistic-lock token to opt into here -- consistent with every other
    AAC-using model in this codebase (``transfer_history``,
    ``shipment_status_history``, ``order_price_freeze``).

Naming convention:
    ``payment_number``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("payment", "number")``, NOT column-level
    ``unique=True``. All three CHECKs use ``ck_index_name`` normally: the
    standard helper output already matches the spec's three literal names
    verbatim (``ck_payment_method``, ``ck_payment_amount_positive``,
    ``ck_payment_unallocated_range``) -- no override needed. Every FK uses
    ``fk_index_name`` normally. The recommended single-column index uses
    ``idx_index_name`` with no override needed. The partial index
    ``idx_payment_unallocated`` is produced by plain
    ``idx_index_name("payment", "unallocated")`` -- the helper's normal
    output already matches the spec's literal name verbatim.

Out of scope for this model (not implemented here):
    * The compensating-reversal-payment correction pattern -- an
      application/ledger-layer concern, the same treatment
      ``inventory_transaction``'s own ``REVERSAL``-row pattern receives
      (no schema-level self-referencing "reversal_of_id" column is spec'd
      for ``payment`` the way it is for ``inventory_transaction`` /
      ``commission_transaction`` -- this table's own spec section names no
      such column).
    * The column-level ``GRANT`` restricting ``UPDATE (unallocated_amount)``
      to the reconciliation service role -- a permissions/deployment-time
      concern, not expressible in the ORM model layer.
    * Range partitioning by ``received_at`` (yearly) -- spec marks this
      optional/future.
    * Any Alembic migration.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name, uq_index_name
from database.types import business_key_type, money_type, state_token_long_type, token_type


class Payment(Base, AppendOnlyAuditColumns):
    """``T19 — payment`` — money received from a customer, independent of any single invoice (Classification: T, AAC per spec)."""

    __tablename__ = "payment"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------- payment_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    payment_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------ customer_id
    customer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("payment", "customer_id", "customer"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------ currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("payment", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------ received_by
    # This table's own spec'd business actor -- distinct from AAC's mixin
    # created_by. See module docstring's dedicated section.
    received_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("payment", "received_by", "app_user"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------------ amount
    # No spec default -- application always supplies it.
    amount: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------------ method
    # Placeholder width -- see module docstring's dedicated section. No
    # default -- every payment must explicitly state its method.
    method: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # --------------------------------------------------------------------- reference
    # Exact spec match AND semantic match -- see module docstring's
    # dedicated section.
    reference: Mapped[str | None] = mapped_column(
        token_type(),
        nullable=True,
    )

    # -------------------------------------------------------------- received_at
    received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ------------------------------------------------------------- unallocated_amount
    # Non-authoritative cache -- see module docstring's dedicated section.
    # No default -- application sets it (typically = amount) at insert.
    unallocated_amount: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "payment_number") so the assembled
        # name is uq_payment_number, not the doubled
        # uq_payment_payment_number that column-level unique=True's
        # implicit convention would produce.
        UniqueConstraint(
            "payment_number",
            name=uq_index_name("payment", "number"),
        ),
        # CHECK: PaymentMethod 5-value vocabulary.
        CheckConstraint(
            "method IN ("
            "'CASH', 'BANK_TRANSFER', 'CHEQUE', 'CARD', 'MOBILE_WALLET'"
            ")",
            name=ck_index_name("payment", "method"),
        ),
        # CHECK: amount strictly positive.
        CheckConstraint(
            "amount > 0",
            name=ck_index_name("payment", "amount_positive"),
        ),
        # CHECK: unallocated_amount bounded between 0 and amount.
        CheckConstraint(
            "unallocated_amount BETWEEN 0 AND amount",
            name=ck_index_name("payment", "unallocated_range"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("payment", "customer_id"),
            "customer_id",
        ),
        # Partial index -- "apply this payment" workflow.
        Index(
            idx_index_name("payment", "unallocated"),
            "customer_id",
            postgresql_where=sa_text("unallocated_amount > 0"),
        ),
    )


__all__ = ["Payment"]
