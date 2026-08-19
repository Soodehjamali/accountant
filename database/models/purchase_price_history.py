"""``H2 — purchase_price_history`` ORM model (immutable input-cost record per receiving transaction).

Authority: ``06_ERD.md``, line 45 → ``H2 — purchase_price_history``::

    H2 — purchase_price_history
    Purpose: Immutable input-cost record per receiving transaction; seeds
             profit baseline (BRF §1).
    PK: id
    FK: product_id → product, lot_id → product_lot (nullable),
        receiving_transaction_id → inventory_transaction
    Important fields: unit_cost, currency_id → currency,
                      costing_method_snapshot, received_at
    Unique: (receiving_transaction_id) (one-to-one)
    Business constraints: immutable; correction only via reversal + new
                          record; FIFO/LIFO/WA resolution uses this
    Classification: H (append-only)

``06_ERD.md`` is H2's sole authority: like every other table with no
dedicated spec section so far (``price_history.py`` (H1), ``discount.py``
(H3), ``generated_document.py`` (M16)), H2 has no detailed section in
``07_DATABASE_SPEC.md``.

``product_id`` / ``lot_id`` / ``receiving_transaction_id`` — all three FKs
real from the outset:
    ``product``, ``product_lot``, and ``inventory_transaction`` all already
    exist in this codebase, so all three FKs are declared as real
    ``ForeignKey()`` constraints from the start. The ERD's ``FK:`` line
    marks only ``lot_id`` as ``(nullable)`` — every input-cost record has a
    product and a receiving transaction that produced it, but not every
    received product is lot-tracked, the same "lot association is optional,
    per-row" reasoning already established for ``product_serial.lot_id``.
    ``product_id`` and ``receiving_transaction_id`` are therefore declared
    ``NOT NULL``.

``receiving_transaction_id`` — column-level ``unique=True``, one-to-one
per direct ERD annotation, NOT composite:
    The ERD's own ``Unique: (receiving_transaction_id) (one-to-one)`` line
    names a single bare column with an explicit "(one-to-one)"
    parenthetical — the same shape already established for
    ``system_config.key`` / ``product_serial.serial_number`` /
    ``attachment.storage_key`` / ``generated_document.storage_key``:
    column-level ``unique=True``, rendering an ordinary
    ``uq_purchase_price_history_receiving_transaction_id`` constraint via
    the shared naming convention, not a composite one. Each
    ``inventory_transaction`` row that represents a receiving event
    produces at most one ``purchase_price_history`` row — the FK plus this
    unique constraint together express a genuine 1:1 relationship at the
    database layer, exactly as the ERD states.

``unit_cost`` — ``money_type()``, per direct instruction:
    Same factory already used for other per-unit monetary amounts in this
    codebase. ``NOT NULL`` — the entire purpose of this table's row is to
    record what a unit cost at receipt; a row with no cost is meaningless.

``currency_id`` — ``ForeignKey("currency.id")``, real FK, ``NOT NULL``:
    ``currency`` already exists in this codebase — the same ordinary,
    already-existing-target FK treatment already given to
    ``price_history.currency_id``. The ERD's ``Important fields:`` line
    gives ``currency_id`` no nullable annotation, and a monetary
    ``unit_cost`` with no currency to denominate it in is meaningless, so
    ``NOT NULL``.

``costing_method_snapshot`` — CHECK-bounded to the exact same
``CostingMethod`` vocabulary as ``costing_method_config.method``:
    Per direct instruction. This column captures, at the moment this
    receiving-cost row is written, which org-level costing method
    (``costing_method_config.method``) was in effect — a point-in-time
    snapshot, not a live reference, which is precisely why this is a plain
    CHECK-bounded string column rather than a ``ForeignKey()`` to
    ``costing_method_config`` (a snapshot must remain stable even if the
    org's live costing method later changes, and ``costing_method_config``
    is itself a *single-row* singleton table per its own docstring — a FK
    to it would say nothing useful about "which method was active back
    then", only "the org has exactly one costing-method row", which is not
    what this column means). Same literal ``PART A`` vocabulary already
    transcribed for ``costing_method_config.method``: ``FIFO`` / ``LIFO`` /
    ``WEIGHTED_AVERAGE``. ``state_token_type()`` (``VARCHAR(16)``) is used
    for the identical reason it was chosen there — ``WEIGHTED_AVERAGE`` is
    exactly 16 characters, the longest member and still an exact fit. A
    ``CheckConstraint`` via ``ck_index_name`` bounds the column to these
    three literal values, the same CHECK-bounded-enum idiom used
    consistently throughout this codebase. Declared ``NOT NULL`` — the
    Business-constraints line states plainly that "FIFO/LIFO/WA resolution
    uses this", so every row must record a real, known method at the time
    it was written.

``received_at`` — a BUSINESS column distinct from AAC's own ``created_at``,
NOT ``DEFAULT now()``, per direct instruction:
    Both columns answer "when did something happen", the same apparent
    redundancy already addressed for ``user_role.assigned_at`` vs. AAC's
    own ``created_at`` (there, resolved by keeping them as two distinct
    columns, since the ERD names ``assigned_at`` as its own
    ``Important fields:`` entry separate from the ``+AAC`` row every
    AAC-using table already carries). The identical "business column
    alongside the mixin's own audit column" pattern applies here: the ERD
    names ``received_at`` directly among ``Important fields:``, separate
    from AAC's own ``created_at``.

    Unlike ``user_role.assigned_at`` (declared ``NOT NULL DEFAULT now()``,
    because most role grants are genuinely real-time actions with
    backdating as an exceptional migration scenario), ``received_at`` here
    is declared ``NOT NULL`` with **no** ``server_default`` at all — a
    deliberate divergence from ``assigned_at``'s own default-now()
    treatment, per direct instruction: the physical goods-receiving event
    this column records routinely happens before the corresponding data
    entry into this system (a warehouse receives a shipment on the dock,
    and the receiving clerk enters it into the system — and therefore this
    row gets inserted — some time afterward, sometimes the next business
    day). If ``received_at`` defaulted to ``now()``, that default would be
    *actively wrong* in the routine case this column exists to handle
    correctly, not merely wrong in an edge case the way a backdated
    ``user_role`` migration would be — silently recording the row's
    insertion time instead of the true physical receiving date would
    directly corrupt the FIFO/LIFO cost-resolution ordering this table's
    own Business-constraints line says depends on it. The caller is
    therefore required to supply the real receiving date explicitly on
    every insert, with no schema-level fallback.

No CHECK beyond ``costing_method_snapshot``:
    All three FKs carry no additional business rule stated in the ERD line
    itself beyond referential integrity; ``unit_cost`` is a plain monetary
    amount with no stated bound; ``received_at`` is a plain timestamp. No
    other vocabulary column exists on this table to CHECK-bound.

Audit-column family — ``AppendOnlyAuditColumns`` (AAC), per direct
instruction, same pattern as ``price_history.py``:
    ``purchase_price_history`` is classified ``H (append-only)`` — the
    ERD's own Business-constraints line states this plainly: "immutable;
    correction only via reversal + new record". Same shape already
    established for ``price_history`` (H1): a row, once written, is never
    ``UPDATE``-d — a correction produces a brand-new compensating row
    rather than mutating the old one, so AAC's own "no ``updated_at`` /
    ``updated_by`` / ``version`` — these rows are never ``UPDATE``-d"
    rationale (``mixins.py``'s own docstring) applies directly.
    ``PurchasePriceHistory`` therefore gets only ``created_at``
    (``TIMESTAMPTZ NOT NULL DEFAULT now()``) and ``created_by`` (nullable
    ``UUID``, no FK per AAC's own documented convention, left as the mixin
    provides it — unmodified here, the same treatment already given to
    ``price_history``/``order_status_history``/``generated_document``'s
    own inherited ``created_by``) from the mixin. No ``__mapper_args__`` /
    ``version_id_col`` is declared — that mechanism belongs to UAC-using
    models only, and AAC provides no ``version`` column at all.

Naming convention:
    All three FKs use ``fk_index_name`` normally --
    ``fk_purchase_price_history_product_id_product_id`` /
    ``fk_purchase_price_history_lot_id_product_lot_id`` /
    ``fk_purchase_price_history_receiving_transaction_id_inventory_transaction_id``
    / ``fk_purchase_price_history_currency_id_currency_id``.
    ``receiving_transaction_id`` uses column-level ``unique=True`` ->
    ``uq_purchase_price_history_receiving_transaction_id`` (see dedicated
    section above -- NOT composite). The vocabulary CHECK uses
    ``ck_index_name`` -> bare descriptor
    ``costing_method_snapshot_values``, rendering
    ``ck_purchase_price_history_costing_method_snapshot_values`` at
    compile time.

Column-type choices:

* ``unit_cost`` -- ``money_type()``.
* ``currency_id`` -- FK to ``currency.id``, ``NOT NULL``.
* ``costing_method_snapshot`` -- ``state_token_type()`` -> ``VARCHAR(16)``,
  CHECK-bounded to ``FIFO`` / ``LIFO`` / ``WEIGHTED_AVERAGE``.
* ``received_at`` -- ``DateTime(timezone=True)``, ``NOT NULL``, no default
  (see dedicated section above).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import money_type, state_token_type


class PurchasePriceHistory(Base, AppendOnlyAuditColumns):
    """``H2 — purchase_price_history`` — immutable input-cost record per receiving transaction (Classification: H, append-only)."""

    __tablename__ = "purchase_price_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("purchase_price_history", "product_id", "product"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------- lot_id
    # Nullable -- per direct ERD annotation "(nullable)" on this FK line.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("purchase_price_history", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------ receiving_transaction_id
    # Column-level unique=True -- 1:1 with inventory_transaction, NOT
    # composite. See module docstring's dedicated section.
    receiving_transaction_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "inventory_transaction.id",
            name=fk_index_name(
                "purchase_price_history",
                "receiving_transaction_id",
                "inventory_transaction",
            ),
        ),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------------- unit_cost
    unit_cost: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # ------------------------------------------------------------ currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("purchase_price_history", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------ costing_method_snapshot
    # Point-in-time snapshot of costing_method_config.method's live value
    # at write time -- deliberately NOT a ForeignKey(). See module
    # docstring's dedicated section.
    costing_method_snapshot: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- received_at
    # Business column, distinct from AAC's own created_at -- deliberately
    # NO server_default (diverges from user_role.assigned_at's own
    # DEFAULT now()). See module docstring's dedicated section.
    received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "costing_method_snapshot IN ('FIFO', 'LIFO', 'WEIGHTED_AVERAGE')",
            name=ck_index_name(
                "purchase_price_history", "costing_method_snapshot_values"
            ),
        ),
    )


__all__ = ["PurchasePriceHistory"]
