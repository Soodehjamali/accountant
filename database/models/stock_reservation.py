"""``T2 — stock_reservation`` ORM model (holds stock against a pending order).

Authority: ``07_DATABASE_SPEC.md`` §T2 — ``T2 — stock_reservation`` **has** a
full detailed section in the physical spec, so the spec is primary authority
here; ``06_ERD.md`` (line 50, F.1/F.4 boundary) is secondary/corroborating
only::

    T2 — stock_reservation
    Purpose: Holds stock against a pending order without decrementing the
        ledger, preventing overselling (BRF §5).
    PK: id (UUID)
    FK: warehouse_id -> warehouse.id; product_id -> product.id;
        lot_id -> product_lot.id (nullable); order_id -> order.id;
        reserved_by -> app_user.id
    Unique: uq_stock_reservation (order_id, warehouse_id, product_id,
        lot_id, state)
    Check: ck_stock_reservation_state (state IN
        ('ACTIVE','RELEASED','CONSUMED','EXPIRED'));
        ck_stock_reservation_qty_positive (reserved_quantity > 0)
    Business constraints: Sigma(reserved_quantity WHERE state='ACTIVE') for
        a given (warehouse_id, product_id, lot_id) must not exceed the
        current available balance (inventory_balance_snapshot.
        quantity_available) -- validated at reservation-creation time by
        the application service; auto-expiry is a scheduled job
        transitioning ACTIVE -> EXPIRED past expires_at; released on order
        cancellation, consumed on shipment.
    Soft Delete Strategy: Not used -- lifecycle modeled via state, not
        deleted_at.
    Audit Strategy: Standard UAC.
    Notes: --

All five FKs are real from the outset -- no deferred-FK deviation:
    Unlike ``T11 — order_line`` (``discount_id`` / ``price_history_id``
    deferred because ``discount`` / ``price_history`` don't exist yet) or
    the earlier state of ``warehouse.responsible_user_id`` /
    ``inventory_transaction.lot_id`` / ``actor_user_id`` (retrofitted once
    ``app_user`` / ``product_lot`` landed), every table this model
    references -- ``warehouse``, ``product``, ``product_lot``, ``order``,
    ``app_user`` -- already exists in this codebase at the time this model
    is written. All five FKs (``warehouse_id``, ``product_id``, ``lot_id``,
    ``order_id``, ``reserved_by``) are therefore declared as real
    ``ForeignKey()`` constraints from the start; there is no deferred-FK
    section to write for this table.

Reserved-word FK target -- ``order_id -> order.id``:
    Reuses the same literal ``"order.id"`` string ``order.py`` /
    ``order_line.py`` already established for referencing the
    reserved-word-named ``order`` table; SQLAlchemy's dialect-aware
    identifier preparer auto-quotes it for PostgreSQL wherever emitted, with
    no extra configuration needed on this model.

``reserved_by`` -- FK to ``app_user.id``, declared directly on this model:
    Distinct from UAC's own mixin-supplied ``created_by`` / ``updated_by``
    (also FKs to ``app_user.id`` as of the ``database/mixins.py`` retrofit)
    -- ``reserved_by`` is this table's own spec'd business column (*"the
    actor who created this specific reservation"*), not a restatement of
    the audit-trail actor. Both are real FKs to the same target table but
    serve different semantic roles, exactly as ``order.py``'s own
    ``created_by``-vs-UAC discussion already establishes for that model.
    Declared directly with an explicit ``name=fk_index_name(...)`` (this
    model's own table name is known at class-definition time, unlike the
    mixin's ``declared_attr`` columns), so no import-order concern applies
    here the way it did for the mixin retrofit.

CRITICAL naming trap -- the unique constraint:
    The spec's literal constraint name is ``uq_stock_reservation`` -- a bare
    table-name-only descriptor, **not**
    ``uq_stock_reservation_order_id_warehouse_id_product_id_lot_id_state``,
    which is what running the five columns through
    ``composite_descriptor`` + ``uq_index_name`` would produce for an
    ordinary 5-column composite. This model instead calls
    ``UniqueConstraint("order_id", "warehouse_id", "product_id", "lot_id",
    "state", name="uq_stock_reservation")`` with the bare literal string --
    the same treatment ``order.py``'s ``uq_order_number`` and
    ``order_line.py``'s ``uq_order_line`` naming traps already received.
    Flagged explicitly so a future edit doesn't "clean this up" by routing
    it through ``composite_descriptor``/``uq_index_name`` and silently
    lengthening the name.

Naming convention:
    The unique constraint is the naming-trap case explained above -- the
    bare literal ``name="uq_stock_reservation"``, NOT
    ``composite_descriptor`` + ``uq_index_name``. Both CHECKs use
    ``ck_index_name`` normally: the standard helper output already matches
    the spec's two literal names verbatim (``ck_stock_reservation_state``,
    ``ck_stock_reservation_qty_positive``) -- no override needed. Every FK
    uses ``fk_index_name`` normally. The recommended single-column index on
    ``order_id`` and the composite index on ``(warehouse_id, product_id,
    lot_id, state)`` use ``idx_index_name`` with ``composite_descriptor`` for
    the latter -- the spec gives no literal override for either (point 8/9
    just describe them), so the helper's normal output is used as-is. The
    partial index ``idx_stock_reservation_active`` is likewise produced by
    plain ``idx_index_name("stock_reservation", "active")`` -- the helper's
    normal output already matches the spec's literal name verbatim, the same
    "no override needed" case ``order.py``'s own ``idx_order_open`` partial
    index already demonstrates.

Column-type choices:

* ``reserved_quantity`` -- ``money_type()`` -> ``NUMERIC(18, 4)``, an exact
  match to the spec's ``NUMERIC(18,4)``. No default: the spec's own column
  row gives no ``DEFAULT`` for this column (only ``state`` has one), so the
  application always supplies it, same treatment as ``order_line.
  qty_ordered`` / ``unit_price``.
* ``state`` -- ``state_token_type()`` -> ``VARCHAR(16)``, an *exact* match
  to the spec's ``VARCHAR(16)`` (not a placeholder, unlike
  ``order.fulfillment_mode``'s ``VARCHAR(24)``-for-``VARCHAR(20)`` case).
  ``NOT NULL DEFAULT 'ACTIVE'`` mirrors ``order.state``'s own
  ``default=`` / ``server_default=sa_text("'...'")`` dual-declaration
  pattern for a string literal default.
* ``expires_at`` -- ``DateTime(timezone=True)``, ``NOT NULL``. No default:
  the spec's column row gives no ``DEFAULT`` (unlike ``order.ordered_at``,
  which has ``server_default=func.now()``) -- *"Auto-expiry deadline"* is a
  computed, forward-looking value the application must always supply
  (e.g. now() + configured TTL), not a value ``now()`` itself could
  correctly default to.

Out of scope for this model (not implemented here):
    * The Sigma(active reserved) <= available-balance validation -- the spec
      explicitly states this is validated by the application service at
      reservation-creation time against ``inventory_balance_snapshot``
      (a cross-table computation, not expressible as a CHECK constraint).
    * The auto-expiry scheduled job (``ACTIVE -> EXPIRED`` past
      ``expires_at``) -- an application/scheduler-level concern, not a
      schema one.
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

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import money_type, state_token_type


class StockReservation(Base, UniversalAuditColumns):
    """``T2 — stock_reservation`` — holds stock against a pending order without decrementing the ledger (Classification: T)."""

    __tablename__ = "stock_reservation"

    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("stock_reservation", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("stock_reservation", "product_id", "product"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------------- lot_id
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("stock_reservation", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # --------------------------------------------------------------- order_id
    # Reserved-word FK target -- reuses order.py's own literal "order.id"
    # string. See module docstring's "Reserved-word FK target" section.
    order_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order.id",
            name=fk_index_name("stock_reservation", "order_id", "order"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- reserved_by
    # This table's own spec'd business actor -- distinct from UAC's mixin
    # created_by/updated_by. See module docstring's dedicated section.
    reserved_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("stock_reservation", "reserved_by", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------- reserved_quantity
    # No default -- the spec gives none; application always supplies it.
    reserved_quantity: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- state
    # Exact-width match to the spec's VARCHAR(16) -- not a placeholder.
    # Mirrors order.state's own default + server_default string-literal
    # pattern.
    state: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
        default="ACTIVE",
        server_default=sa_text("'ACTIVE'"),
    )

    # ----------------------------------------------------------------- expires_at
    # No default -- "Auto-expiry deadline" is a computed, forward-looking
    # value the application must always supply. See module docstring's
    # column-type-choices note.
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Bare literal name, NOT composite_descriptor + uq_index_name.
        UniqueConstraint(
            "order_id",
            "warehouse_id",
            "product_id",
            "lot_id",
            "state",
            name="uq_stock_reservation",
        ),
        # CHECK: state vocabulary.
        CheckConstraint(
            "state IN ('ACTIVE', 'RELEASED', 'CONSUMED', 'EXPIRED')",
            name=ck_index_name("stock_reservation", "state"),
        ),
        # CHECK: reserved_quantity strictly positive.
        CheckConstraint(
            "reserved_quantity > 0",
            name=ck_index_name("stock_reservation", "qty_positive"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("stock_reservation", "order_id"),
            "order_id",
        ),
        # Composite index -- availability computation.
        Index(
            idx_index_name(
                "stock_reservation",
                composite_descriptor(("warehouse_id", "product_id", "lot_id", "state")),
            ),
            "warehouse_id",
            "product_id",
            "lot_id",
            "state",
        ),
        # Partial index -- active reservations, the hot path for
        # availability + the expiry job's expires_at scan.
        Index(
            idx_index_name("stock_reservation", "active"),
            "warehouse_id",
            "product_id",
            "lot_id",
            postgresql_where=sa_text("state = 'ACTIVE'"),
        ),
    )


__all__ = ["StockReservation"]
