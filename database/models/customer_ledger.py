"""``M13 — customer_ledger`` ORM model (Projection — Non-Authoritative).

Authority: ``06_ERD.md``, line 90 → ``M13 — customer_ledger``::

    M13 — customer_ledger
    Purpose: One account header per customer; running balance is a
        read-optimized cache only, never the source of truth (same
        relationship inventory_balance_snapshot T3 has to
        inventory_transaction T1).
    PK: id
    FK: customer_id → customer (1:1)
    Important fields: current_balance (derived cache), currency_id →
        currency, last_reconciled_at, last_entry_seq
    Unique: customer_id
    Business constraints: non-authoritative -- must always equal
        SUM(customer_ledger_entry.signed_amount) for the customer; fully
        rebuildable from the ledger
    Classification: V (projection, non-authoritative)

Same gap as every other table with no dedicated spec section so far
(``discount.py`` (H3), ``price_list.py`` (C3), ``bot_session.py`` (M12),
etc.): ``06_ERD.md`` is ``customer_ledger``'s sole authority --
``customer_ledger`` has no detailed section in ``07_DATABASE_SPEC.md``
(confirmed by search: no ``07_DATABASE_SPEC.md`` mentions of
"customer_ledger" at all).

Deliberate mirror of ``inventory_balance_snapshot.py`` (T3), per the ERD's
own explicit instruction:
    The ERD's own Purpose line states this table has *"the same
    relationship ... T3 has to ... T1"* -- i.e. ``customer_ledger`` is to
    ``customer_ledger_entry`` (T22, the real append-only source of truth,
    not yet built in this codebase) exactly what ``inventory_balance_
    snapshot`` (T3, already built) is to ``inventory_transaction`` (T1): a
    read-optimized, non-authoritative, fully-rebuildable cache header over
    an append-only ledger stream. ``06_ERD.md``'s own correction note
    (line 136) reinforces this explicitly: *"customer_ledger (M13) ... [is]
    explicitly non-authoritative projection[s], computed the same way
    inventory_balance_snapshot (T3) is computed from inventory_transaction
    (T1)."* This model therefore deliberately mirrors
    ``inventory_balance_snapshot.py``'s own structural choices (no audit
    mixin, no CHECK constraints, column-type/default conventions) rather
    than re-deriving them independently -- the same "sibling table, mirror
    the existing precedent" instruction already followed for
    ``customer_contact.py`` mirroring ``representative_contact.py``. Every
    reasoning note on ``inventory_balance_snapshot.py`` about the
    no-audit-mixin / no-CHECK choices applies identically here, restated
    below for this table's own shape.

One important structural simplification vs. ``inventory_balance_snapshot``:
a single ``unique=True`` column, NOT two partial unique indexes:
    ``inventory_balance_snapshot``'s own uniqueness key is
    ``(warehouse_id, product_id, lot_id)`` -- a three-column composite
    where the third column, ``lot_id``, is nullable, which is exactly what
    forced that model into two partial unique indexes (SQL treats every
    ``NULL`` as distinct from every other ``NULL``, so an ordinary
    composite ``UniqueConstraint``/``unique=True`` including a nullable
    column would silently allow unlimited duplicate-NULL rows -- see that
    model's own "CRITICAL naming trap" docstring section for the full
    mechanism). This table has no such complication: its uniqueness key is
    the single column ``customer_id`` alone, and ``customer_id`` is
    ``NOT NULL`` (every ledger header has exactly one owning customer, per
    the ERD's own ``FK: customer_id -> customer (1:1)`` line) -- there is
    no nullable column anywhere in the uniqueness key for NULL-distinctness
    to complicate. A single ordinary column-level ``unique=True`` on
    ``customer_id`` (-> ``uq_customer_ledger_customer_id``) is therefore
    both correct and sufficient, the same simpler treatment
    ``warehouse.code`` / ``product_serial.serial_number`` already use for
    their own single-column global-uniqueness cases -- no partial index,
    no ``postgresql_where``, no naming-trap suffixing needed at all.

FK is real from the outset:
    Both ``customer`` and ``currency`` already exist in this codebase, so
    ``customer_id`` and ``currency_id`` are declared as real
    ``ForeignKey()`` constraints from the start. Neither is marked
    nullable in the ERD's own line, so both are declared ``NOT NULL`` -- a
    ledger header with no owning customer or no denomination currency is
    meaningless.

Audit-mixin decision -- NEITHER UAC NOR AAC, identical reasoning to
``inventory_balance_snapshot.py``:
    The ERD's own classification is the identical parenthetical --
    ``"V (projection, non-authoritative)"`` -- and the same Business
    Constraints framing: *"non-authoritative ... fully rebuildable from the
    ledger"*. This is the second V-classified table in this codebase (after
    ``inventory_balance_snapshot``, the first), so the same reasoning that
    model's own docstring lays out in full applies again here, restated for
    this table's own columns:

    * **UAC is wrong** for the same reason: there is no business "actor" to
      record on this row -- every field here is re-derived from
      ``customer_ledger_entry`` (T22, not yet built) by a reconciliation
      job, not authored by a human or system actor making a decision.
      Attaching ``created_by``/``updated_by`` FKs to ``app_user`` would
      misrepresent a scheduled recomputation as a user-attributable action,
      and ``version``-based optimistic locking would fight the same
      upsert/rebuild refresh strategy ``inventory_balance_snapshot`` itself
      is refreshed by (per ``06_ERD.md`` line 218: *"inventory_balance_
      snapshot, customer_ledger, and kpi_snapshot are all non-authoritative
      caches ... refreshed asynchronously off the event bus"* -- the ERD
      groups this table with ``inventory_balance_snapshot`` explicitly, by
      name, for exactly this refresh-strategy reason).
    * **AAC is also wrong**, for the identical "same row repeatedly
      overwritten in place" reason: this table has exactly one row per
      customer (its own ``Unique: customer_id`` -- a stronger, single-
      column version of the same "one cache row per key, upserted
      repeatedly" shape ``inventory_balance_snapshot`` has per
      ``(warehouse_id, product_id, lot_id)``), not a new row per event.
      AAC's ``created_at``/``created_by`` pair would freeze at the row's
      first upsert while every later reconciliation mutates the same row
      underneath it -- exactly the gap ``last_reconciled_at`` (this
      table's own bespoke column, not a mixin field) exists to fill
      correctly instead.

    **Conclusion:** neither mixin is used, identical to
    ``inventory_balance_snapshot.py``. ``last_reconciled_at`` /
    ``last_entry_seq`` are this table's own purpose-built provenance
    columns -- the correct, narrower audit surface for a re-derivable
    cache, not a placeholder for the generic mixins.

No CHECK constraints -- identical reasoning to
``inventory_balance_snapshot.py``:
    The ERD gives no CHECK for this table (no vocabulary/enum field, and no
    explicit non-negativity instruction the way some other cache-adjacent
    tables received one) -- consistent with ``inventory_balance_snapshot``
    itself also carrying zero CHECK constraints for the identical class of
    reason: this table is explicitly allowed to be transiently
    stale/inconsistent as a cache (the ERD's own words: *"non-
    authoritative ... fully rebuildable"*), with correctness enforced
    upstream on the real ledger (``customer_ledger_entry``, not yet built),
    not here. No ``ck_customer_ledger_...`` constraint of any kind is
    declared -- not even an "obvious" ``current_balance``
    non-negativity bound, since (unlike a physical stock quantity) a
    customer's account balance can legitimately be negative (e.g. a credit
    balance from an overpayment or a pending credit note).

No soft delete -- this table is classified ``V``, not an ``M +
soft-deletable`` table:
    ``customer_ledger`` carries no soft-delete qualifier of any kind (it
    is not even a plain ``M`` -- it is ``V``, a fundamentally different
    projection classification), so no ``deleted_at`` column is declared,
    the same "classification qualifier decides the column" rule already
    applied to ``product_serial.py`` / ``bot_session.py``. A cache header
    with no rows to delete/undelete has no lifecycle for a soft-delete flag
    to express in the first place -- the row simply gets rebuilt/upserted
    from the ledger, matching ``inventory_balance_snapshot``'s own
    *"Soft Delete Strategy: Not applicable -- rows are upserted/rebuilt"*
    note.

Naming convention:
    ``customer_id`` uses column-level ``unique=True`` ->
    ``uq_customer_ledger_customer_id``, mirroring ``warehouse.code`` /
    ``product_serial.serial_number`` (see the dedicated "single unique=True
    column" section above -- NOT ``inventory_balance_snapshot``'s own
    two-partial-index treatment, which this table's shape does not need).
    ``currency_id`` uses ``fk_index_name`` normally ->
    ``fk_customer_ledger_currency_id_currency_id``.

Column-type choices:

* ``current_balance`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, the same
  factory ``order.grand_total`` / ``inventory_balance_snapshot.quantity_
  on_hand`` use for a precise decimal money/quantity value. ``NOT NULL
  DEFAULT 0`` -- mirrors ``inventory_balance_snapshot``'s own quantity
  columns' ``default=0`` + ``server_default=sa_text("0")`` dual-declaration
  pattern (a freshly-created ledger header starts at a zero balance before
  its first reconciliation).
* ``last_reconciled_at`` -- ``DateTime(timezone=True)``, ``NOT NULL``,
  ``server_default=func.now()`` -- identical treatment to
  ``inventory_balance_snapshot.last_reconciled_at``.
* ``last_entry_seq`` -- plain ``sqlalchemy.BigInteger()``, the same
  factory ``inventory_balance_snapshot.last_transaction_seq`` uses for
  its own analogous "how far into the ledger this cache has been
  verified" column. ``NOT NULL DEFAULT 0``.

Out of scope for this model (not implemented here):
    * The reconciliation/projection job itself (event-bus-triggered per
      ``06_ERD.md`` line 218) -- an application/worker-level concern, not
      a schema one, the same treatment ``inventory_balance_snapshot.py``
      already gives its own reconciliation job.
    * ``customer_ledger_entry`` (T22) itself -- the actual append-only
      source-of-truth table this header caches -- has not been built in
      this codebase yet; this task builds only the M13 header.
    * Any Alembic migration.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.naming import fk_index_name
from database.types import money_type


class CustomerLedger(Base):
    """``M13 — customer_ledger`` — one account header per customer, running balance is a read-optimized non-authoritative cache (Classification: V).

    No audit mixin (neither UAC nor AAC) -- see the module docstring's
    dedicated "Audit-mixin decision" section for the full reasoning,
    mirroring ``inventory_balance_snapshot.py`` (T3) exactly, per the
    ERD's own explicit "same relationship as T3 has to T1" instruction.
    This table's own ``last_reconciled_at`` / ``last_entry_seq`` columns
    are its correct, narrower audit surface, not a placeholder for the
    generic mixins.
    """

    __tablename__ = "customer_ledger"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- customer_id
    # Column-level unique=True -- single-column uniqueness key, no partial
    # indexes needed. See module docstring's dedicated section for why
    # this differs from inventory_balance_snapshot's own two-partial-index
    # treatment.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("customer_ledger", "customer_id", "customer"),
        ),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------------- currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("customer_ledger", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------- current_balance
    # Derived cache -- see module docstring's column-type-choices note.
    # Refreshed by the reconciliation job, not computed by the database.
    current_balance: Mapped[decimal.Decimal] = mapped_column(
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

    # --------------------------------------------------------------- last_entry_seq
    last_entry_seq: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )


__all__ = ["CustomerLedger"]
