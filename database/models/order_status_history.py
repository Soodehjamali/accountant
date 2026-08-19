"""``T12 — order_status_history`` ORM model (immutable order state-machine log).

Authority: ``07_DATABASE_SPEC.md`` §H5 (labeled ``H5 (ERD: T12)`` in the
spec's own section header) -- this table **does** have a full detailed
spec section, despite the request's premise; the spec is therefore primary
authority here, the same "spec wins when it has a dedicated section"
convention already applied to ``order`` / ``order_line`` /
``stock_reservation``. ``06_ERD.md`` (line 63, using the ``T12`` label
instead of the spec's ``H5``) is secondary/corroborating only -- both
labels name the same table; the spec's own section header cross-references
the ERD code explicitly (``H5 (ERD: T12)``) so this is not a conflict, just
two numbering schemes for one table::

    H5 (ERD: T12) — order_status_history
    Purpose: Immutable order state-machine log.
    PK: id (UUID)
    FK: order_id → order.id; actor_user_id → app_user.id
    Column Definitions: +AAC; order_id UUID NOT NULL; actor_user_id UUID
        NOT NULL; from_state VARCHAR(24) NOT NULL; to_state VARCHAR(24)
        NOT NULL; event_at TIMESTAMPTZ NOT NULL DEFAULT now(); note TEXT
        NULL
    Unique Constraints: none — chronological append
    Check Constraints: ck_order_status_history_states (from_state IN
        (...OrderState...) AND to_state IN (...OrderState...))
    Business Constraints: Append-only
    Recommended Indexes: btree on order_id
    Composite Indexes: (order_id, event_at)
    Partial Indexes: none
    Partitioning Strategy: Range partition by event_at (monthly), given
        this scales with order volume.
    Soft Delete Strategy: None
    Audit Strategy: Self-auditing — created_by/actor_user_id capture the
        transitioning actor.

Both FKs are real from the outset:
    ``order`` and ``app_user`` both already exist in this codebase, so
    ``order_id`` and ``actor_user_id`` are declared as real
    ``ForeignKey()`` constraints from the start -- no deferred-FK section
    to write for this table (there are none left anywhere in this codebase
    as of the ``price_history`` / ``order_line.price_history_id`` change).

Reserved-word FK target -- ``order_id -> order.id``:
    Reuses the same literal ``"order.id"`` string ``order.py`` /
    ``order_line.py`` / ``stock_reservation.py`` already established for
    referencing the reserved-word-named ``order`` table; SQLAlchemy's
    dialect-aware identifier preparer auto-quotes it for PostgreSQL
    wherever emitted, with no extra configuration needed on this model.

``actor_user_id`` -- distinct from AAC's own ``created_by``:
    Both are real FKs to ``app_user.id``, but they are not a redundant
    pair: ``actor_user_id`` is this table's own spec'd business column
    ("who caused this specific state transition" -- the very actor the
    row exists to record, per the spec's own §13 note: *"created_by/
    actor_user_id capture the transitioning actor"*), while AAC's
    ``created_by`` is the generic append-only audit-trail actor supplied
    by the mixin on every AAC-using table. This is the exact same
    "business column vs. mixin audit column, same target table, different
    semantic role" situation already documented on
    ``stock_reservation.reserved_by``.

``from_state`` / ``to_state`` -- exact-width match, not a placeholder:
    ``state_token_long_type()`` -> ``VARCHAR(24)``, an *exact* match to the
    spec's own ``VARCHAR(24)`` for both columns -- unlike ``order.py``'s own
    use of this same factory for ``sales_channel``/``fulfillment_mode``
    (there, a placeholder for an unavailable exact-width factory), here the
    factory's width is the literal spec width, not a stand-in.

``note`` -- ``sqlalchemy.Text()``, the first unbounded-text column in this
codebase:
    The spec's own column type is literally ``TEXT`` (unbounded), not a
    ``VARCHAR(N)`` -- ``database/types.py`` has no factory for an unbounded
    text column (every existing factory there wraps a fixed-width
    ``VARCHAR``), so this model uses ``sqlalchemy.Text()`` directly rather
    than reaching for the closest bounded-width factory the way
    placeholder-width columns elsewhere in this codebase do (e.g.
    ``order.fulfillment_mode``): a genuinely unbounded spec type has no
    bounded factory to approximate it, and approximating it with a
    ``VARCHAR(N)`` would silently impose a length limit the spec does not
    ask for. Declared nullable per spec (``NULL``) -- most transitions need
    no free-text annotation.

``event_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment already given to
    ``order.ordered_at`` / ``price_list``'s absence of such a column /
    ``inventory_balance_snapshot.last_reconciled_at``.

No ``UniqueConstraint`` -- explicit per spec:
    Spec §5 states plainly *"none — chronological append"* -- unlike
    ``discount``/``price_list``'s absent-``Unique:``-line cases (where no
    constraint is fabricated due to *silence*), this spec entry
    affirmatively states there is none, removing any ambiguity. No
    ``UniqueConstraint`` is declared.

CHECK constraint -- ``OrderState`` vocabulary, transcribed verbatim from
``order.py``:
    ``ck_order_status_history_states`` bounds **both** ``from_state`` and
    ``to_state`` to the same 13-value ``OrderState`` vocabulary
    ``order.py``'s own ``ck_order_state`` CHECK already enforces on
    ``order.state`` -- transcribed verbatim (same value list, same order)
    from that model's own CHECK text rather than retyped independently, to
    guarantee the two CHECKs can never silently drift apart. Unlike
    ``order.state`` (a single column bound to one CHECK), this table binds
    **two** columns to the *same* vocabulary in **one** combined CHECK
    (spec: *"ck_order_status_history_states (from_state IN (...) AND
    to_state IN (...))"* -- one named constraint, not two), the same
    "one combined CHECK across multiple columns" treatment
    ``order_line.ck_order_line_qty_nonneg`` already uses for its own four
    qty columns.

Indexes:
    Recommended single-column ``idx_order_status_history_order_id`` on
    ``order_id`` (spec §8) via ``idx_index_name``, plus a composite
    ``(order_id, event_at)`` index (spec §9) via ``idx_index_name`` +
    ``composite_descriptor`` -- an ordinary composite case, the spec gives
    no literal name override for it (unlike ``order_line``/
    ``stock_reservation``'s bare-literal naming traps), so the standard
    helper output is used as-is. No partial index (spec §10: none).

Out of scope for this model (not implemented here):
    * Range partitioning by ``event_at`` (monthly) -- spec §11 marks this a
      physical-design/migration-time decision, the same treatment already
      given to every other table's own partitioning-strategy note in this
      codebase (e.g. ``order_line``, ``inventory_transaction``).
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    Classification ``H`` (spec's own header: ``H5``), Business Constraints
    §7: *"Append-only"* -- the same unqualified append-only classification
    ``price_history`` (H1) and ``inventory_transaction`` (T+H) already
    carry, both of which use AAC. ``OrderStatusHistory`` therefore gets
    ``created_at`` / ``created_by`` only -- no ``updated_at`` /
    ``updated_by`` / ``version``, and consequently no ``__mapper_args__ =
    {"version_id_col": ...}`` (this table's rows are never updated at all,
    not even the single documented later-write exception
    ``price_history.effective_to`` / ``inventory_transaction.is_reversed``
    have -- a state-transition log row is complete and final the instant
    it is inserted).

Naming convention:
    Both FKs use ``fk_index_name`` normally
    (``fk_order_status_history_order_id_order_id``,
    ``fk_order_status_history_actor_user_id_app_user_id``). The CHECK uses
    ``ck_index_name`` normally -> ``ck_order_status_history_states``. The
    recommended index and composite index both use ``idx_index_name``
    (the latter with ``composite_descriptor``) -- no literal override
    needed for either. There is no ``UniqueConstraint`` -- see the section
    above.

Column-type choices:

* ``from_state`` / ``to_state`` -- ``state_token_long_type()`` ->
  ``VARCHAR(24)`` (exact spec match, see dedicated note above).
* ``event_at`` -- ``DateTime(timezone=True)``, ``NOT NULL DEFAULT now()``.
* ``note`` -- ``sqlalchemy.Text()``, nullable (see dedicated note above).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import state_token_long_type


class OrderStatusHistory(Base, AppendOnlyAuditColumns):
    """``T12 — order_status_history`` — immutable order state-machine log (Classification: H)."""

    __tablename__ = "order_status_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- order_id
    # Reserved-word FK target -- reuses order.py's own literal "order.id"
    # string. See module docstring's "Reserved-word FK target" section.
    order_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order.id",
            name=fk_index_name("order_status_history", "order_id", "order"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- actor_user_id
    # This table's own spec'd business actor -- distinct from AAC's mixin
    # created_by. See module docstring's dedicated section.
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("order_status_history", "actor_user_id", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- from_state
    # Exact-width match to the spec's VARCHAR(24) -- not a placeholder.
    from_state: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # ---------------------------------------------------------------- to_state
    to_state: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # --------------------------------------------------------------- event_at
    event_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # -------------------------------------------------------------------- note
    # sqlalchemy.Text() -- first unbounded-text column in this codebase.
    # See module docstring's dedicated section. Nullable per spec.
    note: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: OrderState vocabulary on BOTH from_state and to_state, in
        # ONE combined constraint -- transcribed verbatim from order.py's
        # own ck_order_state CHECK text. See module docstring's dedicated
        # section.
        CheckConstraint(
            "from_state IN ("
            "'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'RESERVED', "
            "'FULFILLING', 'SHIPPED', 'INVOICED', 'PAID', 'COMPLETED', "
            "'CANCELLED', 'BACKORDERED', 'PARTIALLY_FULFILLED', 'RETURNED'"
            ") AND to_state IN ("
            "'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'RESERVED', "
            "'FULFILLING', 'SHIPPED', 'INVOICED', 'PAID', 'COMPLETED', "
            "'CANCELLED', 'BACKORDERED', 'PARTIALLY_FULFILLED', 'RETURNED'"
            ")",
            name=ck_index_name("order_status_history", "states"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("order_status_history", "order_id"),
            "order_id",
        ),
        # Composite index -- (order_id, event_at), ordinary composite case.
        Index(
            idx_index_name(
                "order_status_history",
                composite_descriptor(("order_id", "event_at")),
            ),
            "order_id",
            "event_at",
        ),
    )


__all__ = ["OrderStatusHistory"]
