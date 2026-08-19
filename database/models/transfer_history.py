"""``H4 (ERD: T6) — transfer_history`` ORM model (immutable transfer state-change log).

Authority: ``07_DATABASE_SPEC.md`` §H4 (labeled ``H4 (ERD: T6)`` in the
spec's own section header) -- this table **does** have a full detailed spec
section, so the spec is primary authority here, the same "spec wins when it
has a dedicated section" convention already applied to
``order_status_history`` (H5 / ERD T12). ``06_ERD.md`` (line 55, using the
``T6`` label instead of the spec's ``H4``) is secondary/corroborating only --
both labels name the same table; the spec's own section header
cross-references the ERD code explicitly (``H4 (ERD: T6)``), so this is not
a conflict, just two numbering schemes for one table::

    H4 (ERD: T6) — transfer_history
    Purpose: Immutable state-change log for each transfer.
    PK: id (UUID)
    FK: stock_transfer_id -> stock_transfer.id; actor_user_id -> app_user.id
    Column Definitions: +AAC; stock_transfer_id UUID NOT NULL; actor_user_id
        UUID NOT NULL; from_state VARCHAR(20) NOT NULL; to_state VARCHAR(20)
        NOT NULL; event_at TIMESTAMPTZ NOT NULL DEFAULT now(); note TEXT
        NULL
    Unique Constraints: none — chronological append
    Check Constraints: ck_transfer_history_states (from_state IN
        (...TransferState values...) AND to_state IN (...TransferState
        values...))
    Business Constraints: Append-only, no UPDATE/DELETE
    Recommended Indexes: btree on stock_transfer_id
    Composite Indexes: (stock_transfer_id, event_at)
    Partial Indexes: none
    Partitioning Strategy: Range partition by event_at (quarterly) once
        volume warrants it -- lower priority than T1/T22/T23.
    Soft Delete Strategy: None — immutable, append-only
    Audit Strategy: created_by (AAC) equals actor_user_id; this table is
        itself the audit record for transfer state changes.

Owned by the StockTransfer aggregate (``06_ERD.md``: *"StockTransfer (root:
stock_transfer) -- owns transfer_line, transfer_history"*) via
``stock_transfer_id``, the same aggregate ``transfer_line.py`` (T5) belongs
to.

Both FKs are real from the outset:
    ``stock_transfer`` (T4, this same change) and ``app_user`` both exist,
    so ``stock_transfer_id`` and ``actor_user_id`` are declared as real
    ``ForeignKey()`` constraints from the start -- no deferred-FK section to
    write for this table.

Non-reserved-word FK target -- ``stock_transfer_id -> stock_transfer.id``:
    ``stock_transfer`` is an ordinary identifier, unlike ``order``'s own
    reserved-word situation on ``order_status_history.order_id`` -- no
    quoting concern applies here.

``actor_user_id`` -- distinct from AAC's own ``created_by``:
    Both are real FKs to ``app_user.id``, but they are not a redundant
    pair: ``actor_user_id`` is this table's own spec'd business column
    ("who caused this specific state transition" -- the very actor the row
    exists to record, per the spec's own §13 note: *"created_by (AAC)
    equals actor_user_id; this table is itself the audit record"*), while
    AAC's ``created_by`` is the generic append-only audit-trail actor
    supplied by the mixin on every AAC-using table. This is the exact same
    "business column vs. mixin audit column, same target table, different
    semantic role" situation ``order_status_history.actor_user_id`` already
    documents.

``from_state`` / ``to_state`` -- ``VARCHAR(20)`` per spec, placeholder
width, NOT an exact match:
    Unlike ``order_status_history.from_state``/``to_state`` (an exact
    ``VARCHAR(24)`` match to that table's own spec width), this table's spec
    width is ``VARCHAR(20)`` and no ``database.types`` factory produces
    exactly 20 characters (``state_token_type()`` -> 16,
    ``state_token_long_type()`` -> 24). ``state_token_long_type()`` is used
    as the closest existing factory -- the same placeholder treatment
    ``stock_transfer.state`` / ``stock_transfer.ownership_mode_snapshot``
    (this same change) already receive for their own ``VARCHAR(20)`` spec
    widths.

``note`` -- ``sqlalchemy.Text()``, same unbounded-text treatment as
``order_status_history.note``:
    The spec's own column type is literally ``TEXT`` (unbounded), not a
    ``VARCHAR(N)`` -- ``database/types.py`` has no factory for an unbounded
    text column, so this model uses ``sqlalchemy.Text()`` directly, the same
    treatment ``order_status_history.note`` already established for this
    codebase's first unbounded-text column. Declared nullable per spec.

``event_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment ``order_status_history.event_at``
    / ``stock_transfer.requested_at`` already receive.

No ``UniqueConstraint`` -- explicit per spec:
    Spec §5 states plainly *"none — chronological append"* -- the same
    affirmative-absence treatment ``order_status_history`` already
    documents for its own identical unique-constraints line. No
    ``UniqueConstraint`` is declared.

CHECK constraint -- ``TransferState`` vocabulary, transcribed verbatim from
``stock_transfer.py``:
    ``ck_transfer_history_states`` bounds **both** ``from_state`` and
    ``to_state`` to the same 9-value ``TransferState`` vocabulary
    ``stock_transfer.py``'s own ``ck_stock_transfer_state`` CHECK already
    enforces on ``stock_transfer.state`` -- transcribed verbatim (same value
    list, same order) from that model's own CHECK text rather than retyped
    independently, to guarantee the two CHECKs can never silently drift
    apart. This is the exact same treatment
    ``order_status_history.ck_order_status_history_states`` already gives
    the ``OrderState`` vocabulary relative to ``order.ck_order_state``: one
    combined CHECK across both columns (spec: *"ck_transfer_history_states
    (from_state IN (...) AND to_state IN (...))"* -- one named constraint,
    not two).

Indexes:
    Recommended single-column ``idx_transfer_history_stock_transfer_id`` on
    ``stock_transfer_id`` (spec §8) via ``idx_index_name``, plus a composite
    ``(stock_transfer_id, event_at)`` index (spec §9) via ``idx_index_name``
    + ``composite_descriptor`` -- an ordinary composite case, the spec gives
    no literal name override for it, so the standard helper output is used
    as-is. No partial index (spec §10: none).

Out of scope for this model (not implemented here):
    * Range partitioning by ``event_at`` (quarterly) -- spec §11 marks this
      a physical-design/migration-time decision, the same treatment every
      other table's own partitioning-strategy note in this codebase already
      receives.
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    Classification ``H`` (spec's own header: ``H4``), Business Constraints
    §7: *"Append-only, no UPDATE/DELETE"* -- the same unqualified
    append-only classification ``order_status_history`` (H5) already
    carries, which also uses AAC. ``TransferHistory`` therefore gets
    ``created_at`` / ``created_by`` only -- no ``updated_at`` /
    ``updated_by`` / ``version``, and consequently no ``__mapper_args__ =
    {"version_id_col": ...}`` -- a state-transition log row is complete and
    final the instant it is inserted.

Naming convention:
    Both FKs use ``fk_index_name`` normally
    (``fk_transfer_history_stock_transfer_id_stock_transfer_id``,
    ``fk_transfer_history_actor_user_id_app_user_id``). The CHECK uses
    ``ck_index_name`` normally -> ``ck_transfer_history_states``. The
    recommended index and composite index both use ``idx_index_name`` (the
    latter with ``composite_descriptor``) -- no literal override needed for
    either. There is no ``UniqueConstraint`` -- see the section above.
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


class TransferHistory(Base, AppendOnlyAuditColumns):
    """``H4 (ERD: T6) — transfer_history`` — immutable transfer state-change log (Classification: H)."""

    __tablename__ = "transfer_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ---------------------------------------------------- stock_transfer_id
    stock_transfer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "stock_transfer.id",
            name=fk_index_name("transfer_history", "stock_transfer_id", "stock_transfer"),
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
            name=fk_index_name("transfer_history", "actor_user_id", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- from_state
    # Placeholder width -- see module docstring's dedicated section.
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
    # sqlalchemy.Text() -- same unbounded-text treatment as
    # order_status_history.note. Nullable per spec.
    note: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: TransferState vocabulary on BOTH from_state and to_state,
        # in ONE combined constraint -- transcribed verbatim from
        # stock_transfer.py's own ck_stock_transfer_state CHECK text. See
        # module docstring's dedicated section.
        CheckConstraint(
            "from_state IN ("
            "'DRAFT', 'PENDING', 'APPROVED', 'DISPATCHED', 'IN_TRANSIT', "
            "'RECEIVED', 'PARTIAL_RECEIVED', 'CLOSED', 'CANCELLED'"
            ") AND to_state IN ("
            "'DRAFT', 'PENDING', 'APPROVED', 'DISPATCHED', 'IN_TRANSIT', "
            "'RECEIVED', 'PARTIAL_RECEIVED', 'CLOSED', 'CANCELLED'"
            ")",
            name=ck_index_name("transfer_history", "states"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("transfer_history", "stock_transfer_id"),
            "stock_transfer_id",
        ),
        # Composite index -- (stock_transfer_id, event_at), ordinary
        # composite case.
        Index(
            idx_index_name(
                "transfer_history",
                composite_descriptor(("stock_transfer_id", "event_at")),
            ),
            "stock_transfer_id",
            "event_at",
        ),
    )


__all__ = ["TransferHistory"]
