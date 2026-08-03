"""``T1 — inventory_transaction`` ORM model (the inventory ledger).

Authority: ``07_DATABASE_SPEC.md`` §T1 — ``T1 — inventory_transaction`` **is**
one of the tables that has a full detailed section in the physical spec
(unlike M1-M12), so the spec is primary authority here; ``06_ERD.md``
(F.1 — Inventory Ledger) is secondary/corroborating, not the source of
first resort::

    T1 — inventory_transaction
    Purpose: The sole authoritative source of inventory truth (Domain-Model
             correction #1). All stock levels are projections of this table;
             nothing else may hold an editable balance.
    PK: id (UUID)
    FK: product_id -> product.id; lot_id -> product_lot.id (nullable);
        warehouse_id -> warehouse.id; movement_type_id -> movement_type_ref.id;
        actor_user_id -> app_user.id; reason_code_id -> reason_code_ref.id
        (nullable); reversal_of_id -> inventory_transaction.id (nullable,
        self-ref); reference_type + reference_id (polymorphic)
    Unique: uq_inventory_transaction_seq (warehouse_id, sequence_no);
            uq_inventory_transaction_hash (row_hash)
    Check: ck_inventory_transaction_qty_nonzero (signed_quantity <> 0)
    Business constraints: no UPDATE/DELETE grants (permissions layer);
        signed_quantity's sign must match movement_type_ref.sign (BEFORE
        INSERT trigger, cross-table -- out of scope here); projected net
        balance must never go negative (BEFORE INSERT trigger, cross-table
        -- out of scope here); at most one non-reversed REVERSAL row may
        reference a given original row -- partial unique index
        uq_inventory_transaction_one_reversal.

Retrofitted ForeignKey() columns -- ``lot_id`` / ``actor_user_id``:
    Both ``product_lot`` (M2) and ``app_user`` (M10) now exist in this
    codebase, so the deferred-FK deviation previously documented here (the
    same one still open for other columns, e.g. ``warehouse``'s remaining
    deferred columns) is resolved for this table's two columns:

    * ``lot_id`` -> ``product_lot.id`` -- real ``ForeignKey``, **nullable**
      (per spec: ``lot_id UUID NULL``), named via ``fk_index_name`` as usual.
    * ``actor_user_id`` -> ``app_user.id`` -- real ``ForeignKey``, **NOT
      NULL** (per spec: *"FK, mandatory (no anonymous ledger writes)"*),
      named via ``fk_index_name`` as usual.

Flagged, not resolved -- AAC/``actor_user_id`` apparent duplication:
    ``AppendOnlyAuditColumns`` (AAC)'s own ``created_by`` is **nullable by
    design** (``NULL`` = system-generated row -- see ``mixins.py``). This
    table's spec *also* demands a separate, **mandatory**
    ``actor_user_id UUID NOT NULL``, explicitly annotated *"no anonymous
    ledger writes"*. Both columns are implemented exactly as spec'd, side by
    side, rather than collapsed into one: ``created_by`` (nullable, AAC) and
    ``actor_user_id`` (NOT NULL, this table's own spec'd column) now appear
    to say two different things about who may post an unattributed row. This
    is noted here as an apparent spec ambiguity for later product-owner
    clarification -- it is not silently resolved in either direction by this
    model.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    ``inventory_transaction`` is an append-only ledger (Classification:
    T + H, immutable) -- it gets ``created_at`` / ``created_by`` only, with
    **no** ``updated_at`` / ``updated_by`` / ``version``, per
    ``database.mixins.AppendOnlyAuditColumns``'s own documented rationale
    (append-only rows have no second mutation, no second actor, no
    optimistic-lock contention to guard).

Naming convention -- three literal, spec-mandated overrides:
    The spec names three constraints **literally**, not via the project's
    usual composite-descriptor naming helper:

    * ``UniqueConstraint(warehouse_id, sequence_no)`` -> the spec literally
      names this ``uq_inventory_transaction_seq``, which is *not* what the
      naming module's composite-descriptor helper would auto-derive from the
      two-column tuple. The literal string is passed straight to ``name=``,
      deviating from the helper on purpose because the spec's name is
      authoritative and does not match the helper's usual output shape.
    * ``UniqueConstraint(row_hash)`` -> likewise spec-literal:
      ``uq_inventory_transaction_hash``.
    * The partial unique index on ``reversal_of_id`` -> the spec literally
      names it ``uq_inventory_transaction_one_reversal`` -- note the ``uq_``
      prefix despite this being an ``Index`` object, not a
      ``UniqueConstraint``. This is a second, distinct override: it does
      not go through ``idx_index_name`` (which would produce an ``idx_``
      prefix, as it does for every other index below); the spec's literal
      name wins over the helper's usual prefix convention.

    Every *other* index below uses ``idx_index_name`` normally (the helper's
    usual ``idx_`` prefix applies with no override): the two single-column
    recommended indexes on ``product_id`` / ``lot_id``, the composite index
    ``(warehouse_id, product_id, lot_id, sequence_no)``, the composite index
    ``(reference_type, reference_id)``, and the partial index
    ``idx_inventory_transaction_unreversed``.

    FK columns that *do* have a real ``ForeignKey()`` (``product_id``,
    ``lot_id``, ``warehouse_id``, ``movement_type_id``, ``actor_user_id``,
    ``reason_code_id``, ``currency_id``, ``reversal_of_id``) use
    ``fk_index_name`` as usual.
    ``reversal_of_id`` is an unqualified self-reference
    (``ForeignKey("inventory_transaction.id", ...)``), the same pattern as
    ``product.variant_of_id``.

Column-type choices:

* ``reference_type`` -- ``type_token_type()`` -> ``VARCHAR(40)``, matching
  the spec's polymorphic-discriminator width exactly.
* ``signed_quantity`` -- ``money_type()`` -> ``NUMERIC(18, 4)``; this exact
  column is named in ``money_type()``'s own docstring.
* ``unit_cost`` -- ``cost_type()`` -> ``NUMERIC(18, 6)``; this exact column
  is named in ``cost_type()``'s own docstring.
* ``prev_hash`` / ``row_hash`` -- plain ``sqlalchemy.CHAR(HASH_HEX_LENGTH)``.
  ``HASH_HEX_LENGTH`` is imported directly from ``database.constants``
  rather than from a ``database.types`` factory: ``types.py``'s own
  docstring flags ``HASH_HEX_LENGTH`` as a deliberate omission from that
  module (it is a module-level constant, not a ``NumericPrecision`` /
  ``StringLength`` member), explicitly leaving the model layer to consume it
  directly -- which is what this model does.
* ``sequence_no`` -- ``sqlalchemy.BigInteger`` (spec: ``BIGINT``).
* ``occurred_at`` -- ``DateTime(timezone=True)``, ``NOT NULL``,
  ``server_default=func.now()`` -- spec-mandated exact shape.
* ``is_reversed`` -- ``Boolean()``, ``NOT NULL``, ``default=False``,
  ``server_default=sa_text("false")`` -- mirrors ``Currency.is_base``
  exactly (see ``currency.py``).

Explicitly OUT OF SCOPE for this model (not implemented here):
    * The ``BEFORE INSERT`` triggers for sign-matching
      (``signed_quantity`` vs. ``movement_type_ref.sign``) and
      negative-balance validation -- the spec itself states these cannot be
      CHECK constraints (cross-table); they are migration/DDL-level
      concerns.
    * Monthly range partitioning by ``occurred_at`` -- an
      Alembic/DDL-level concern.
    * Any Alembic migration.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.constants import HASH_HEX_LENGTH
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import cost_type, money_type, type_token_type


class InventoryTransaction(Base, AppendOnlyAuditColumns):
    """``T1 — inventory_transaction`` — the inventory ledger (Classification: T + H, immutable)."""

    __tablename__ = "inventory_transaction"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("inventory_transaction", "product_id", "product"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------------- lot_id
    # Real FK -- product_lot (M2) now exists.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("inventory_transaction", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("inventory_transaction", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------- movement_type_id
    movement_type_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "movement_type_ref.id",
            name=fk_index_name("inventory_transaction", "movement_type_id", "movement_type_ref"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- actor_user_id
    # Real FK -- app_user (M10) now exists. NOT NULL per spec: "mandatory,
    # no anonymous ledger writes" -- see the module docstring's
    # AAC/actor_user_id flag above.
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("inventory_transaction", "actor_user_id", "app_user"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ reason_code_id
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "reason_code_ref.id",
            name=fk_index_name("inventory_transaction", "reason_code_id", "reason_code_ref"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------- reference_type
    # Polymorphic discriminator (e.g. "order", "stock_transfer").
    reference_type: Mapped[str | None] = mapped_column(
        type_token_type(),
        nullable=True,
    )

    # --------------------------------------------------------------- reference_id
    # Polymorphic target id -- no ForeignKey() possible (can't reference a
    # single table).
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        nullable=True,
    )

    # ---------------------------------------------------------------- sequence_no
    # Monotonic per warehouse, assigned by trigger/sequence (DDL concern,
    # out of scope here).
    sequence_no: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )

    # ------------------------------------------------------------ signed_quantity
    signed_quantity: Mapped[float] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ----------------------------------------------------------------- unit_cost
    unit_cost: Mapped[float] = mapped_column(
        cost_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("inventory_transaction", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- occurred_at
    occurred_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ---------------------------------------------------------------- prev_hash
    # CHAR(HASH_HEX_LENGTH) imported directly from database.constants --
    # types.py's own docstring flags this as a deliberate module-layer
    # omission for the model to handle (see module docstring above).
    prev_hash: Mapped[str | None] = mapped_column(
        CHAR(HASH_HEX_LENGTH),
        nullable=True,
    )

    # ----------------------------------------------------------------- row_hash
    row_hash: Mapped[str] = mapped_column(
        CHAR(HASH_HEX_LENGTH),
        nullable=False,
    )

    # ------------------------------------------------------------ reversal_of_id
    # Self-ref FK, unqualified table name -- same pattern as
    # product.variant_of_id. Set only on REVERSAL rows.
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "inventory_transaction.id",
            name=fk_index_name("inventory_transaction", "reversal_of_id", "inventory_transaction"),
        ),
        nullable=True,
    )

    # ---------------------------------------------------------------- is_reversed
    # Mirrors Currency.is_base exactly.
    is_reversed: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    __table_args__ = (
        # CHECK: signed_quantity must never be zero.
        CheckConstraint(
            "signed_quantity <> 0",
            name=ck_index_name("inventory_transaction", "qty_nonzero"),
        ),
        # UNIQUE -- spec-literal name override (see module docstring):
        # NOT the composite-descriptor helper's auto-derived name.
        UniqueConstraint(
            "warehouse_id",
            "sequence_no",
            name="uq_inventory_transaction_seq",
        ),
        # UNIQUE -- spec-literal name override, same reasoning.
        UniqueConstraint(
            "row_hash",
            name="uq_inventory_transaction_hash",
        ),
        # Partial unique index -- spec-literal name override: "uq_" prefix
        # despite being an Index object (idx_index_name's usual "idx_"
        # prefix does NOT apply here; the spec's literal name wins).
        Index(
            "uq_inventory_transaction_one_reversal",
            "reversal_of_id",
            unique=True,
            postgresql_where=sa_text("reversal_of_id IS NOT NULL"),
        ),
        # Recommended single-column indexes -- normal idx_index_name usage.
        Index(
            idx_index_name("inventory_transaction", "product_id"),
            "product_id",
        ),
        Index(
            idx_index_name("inventory_transaction", "lot_id"),
            "lot_id",
        ),
        # Composite index -- the balance-projection query.
        Index(
            idx_index_name("inventory_transaction", "warehouse_product_lot_seq"),
            "warehouse_id",
            "product_id",
            "lot_id",
            "sequence_no",
        ),
        # Composite index -- trace a ledger row back to its originating document.
        Index(
            idx_index_name("inventory_transaction", "reference_type_id"),
            "reference_type",
            "reference_id",
        ),
        # Partial index -- the hot path for current-balance computation.
        Index(
            idx_index_name("inventory_transaction", "unreversed"),
            "warehouse_id",
            "product_id",
            "lot_id",
            postgresql_where=sa_text("is_reversed = false"),
        ),
    )


__all__ = ["InventoryTransaction"]
