"""``H4 (ERD id) — invoice_history`` ORM model (immutable invoice state-change log).

Authority: ``07_DATABASE_SPEC.md`` §H4 (spec's own section header:
``H4 (ERD id) — invoice_history``; distinct from the *other* ``H4`` label
already used elsewhere in this codebase for ``transfer_history``
(``H4 (ERD: T6) — transfer_history``) -- the spec reuses the bare ``H4``
prefix twice, once per its own numbering track for ERD-id-less append-only
tables and once for a table that *does* have an ERD numeric id (``T6``);
this task's own prompt explicitly flags this exact collision and instructs
following the spec's real table, not the code a prior task mis-assigned to
``transfer_history`` -- confirmed directly against the live spec text
fetched for this change, not assumed) -- this table **does** have a full
detailed spec section, so the spec is primary authority here;
``06_ERD.md`` (F.6 — Finance / Invoicing) is secondary/corroborating
only::

    H4 (ERD id) — invoice_history
    Purpose: Immutable state-change log for invoices.
    PK: id (UUID)
    FK: invoice_id -> invoice.id; actor_user_id -> app_user.id
    Column Definitions: +AAC; invoice_id UUID NOT NULL; actor_user_id UUID
        NOT NULL; from_state VARCHAR(20) NOT NULL; to_state VARCHAR(20) NOT
        NULL; event_at TIMESTAMPTZ NOT NULL DEFAULT now(); note TEXT NULL
    Unique Constraints: none — chronological append
    Check Constraints: ck_invoice_history_states (from_state IN
        (...InvoiceState...) AND to_state IN (...InvoiceState...))
    Business Constraints: Append-only
    Recommended Indexes: btree on invoice_id
    Composite Indexes: (invoice_id, event_at)
    Partial Indexes: none
    Partitioning Strategy: Range partition by event_at (monthly), tracks
        invoice volume
    Soft Delete Strategy: None
    Audit Strategy: Self-auditing

Owned by ``invoice`` (T17, already present in this codebase) via
``invoice_id`` -- the same "immutable state-log child of a mutable UAC
header" relationship ``order_status_history`` has to ``order`` and
``transfer_history`` has to ``stock_transfer``. Referenced directly by
``invoice.py``'s own §13 Audit Strategy line ("state transitions mirrored
to ``invoice_history`` (H4)"), confirming the two were always meant to pair
even though ``invoice_history`` itself is only being built in this change.

Non-reserved-word FK targets -- ``invoice_id -> invoice.id`` /
``actor_user_id -> app_user.id``:
    Both are ordinary identifiers, no quoting concerns for either FK.

``actor_user_id`` -- ``NOT NULL``, unlike several sibling history tables'
own nullable actor columns:
    Unlike ``shipment_status_history.actor_user_id`` /
    ``notification_history.actor_user_id`` (both nullable, "for automated
    tracking pings" / "for automated system retries"), this table's own
    spec marks ``actor_user_id`` ``NOT NULL`` -- every invoice state
    transition is spec'd to have a real human actor, with no
    "system-generated, no actor" case carved out the way those other
    tables' own spec text explicitly does. Distinct from AAC's own mixin
    ``created_by`` for the same "business column vs. mixin audit column"
    reason those other tables document, just without the nullability
    divergence.

``from_state`` / ``to_state`` -- ``VARCHAR(20)`` per spec, placeholder
width, NOT an exact match:
    Same situation as ``invoice.state`` itself (this table's own parent
    column) -- no ``database.types`` factory produces exactly 20
    characters (``state_token_type()`` -> 16, ``state_token_long_type()``
    -> 24). ``state_token_long_type()`` is used as the closest existing
    factory, the same placeholder ``invoice.state`` already receives.

``InvoiceState`` vocabulary -- transcribed verbatim from ``invoice.py``'s
own CHECK:
    ``ck_invoice_history_states`` bounds both ``from_state`` and
    ``to_state`` to the same 6-value vocabulary ``invoice.py``'s own
    ``ck_invoice_state`` CHECK already enforces on ``invoice.state``
    (``'DRAFT','ISSUED','PARTIALLY_PAID','PAID','CLOSED_CORRECTED','VOID'``)
    -- transcribed verbatim (same value list, same order) from that
    model's own CHECK text rather than retyped independently, to guarantee
    the two CHECKs can never silently drift apart. The same treatment
    ``transfer_history.ck_transfer_history_states`` already gives relative
    to ``stock_transfer.ck_stock_transfer_state``. Both columns are
    ``NOT NULL`` and checked together in one combined constraint -- the
    same "no inaugural-NULL case" shape ``notification_history`` has,
    unlike ``shipment_status_history``'s asymmetric to-state-only
    treatment.

``note`` -- ``sqlalchemy.Text()``, same unbounded-text treatment as every
other ``*_history.note`` column in this codebase:
    The spec's own column type is literally ``TEXT`` (unbounded). Nullable
    per spec, no CHECK conditioning it on ``to_state`` (unlike
    ``shipment_status_history``'s own ``failed_note`` CHECK) -- this
    table's own §6 CHECK text names only the states CHECK, nothing else.

``event_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment every other ``*_history
    .event_at`` column in this codebase receives.

No ``UniqueConstraint`` -- explicit per spec:
    Spec §5: *"none — chronological append"* -- the same affirmative-absence
    treatment every sibling ``*_history`` table in this codebase already
    documents.

Indexes:
    Recommended single-column ``idx_invoice_history_invoice_id`` on
    ``invoice_id`` (spec §8) via ``idx_index_name``, plus a composite
    ``(invoice_id, event_at)`` index (spec §9) via ``idx_index_name`` +
    ``composite_descriptor`` -- an ordinary composite case, the spec gives
    no literal name override for it. No partial index (spec §10: none).

Out of scope for this model (not implemented here):
    * Range partitioning by ``event_at`` (monthly) -- spec §11 marks this a
      physical-design/migration-time decision.
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    The spec's own §4 Column Definitions table opens with ``+AAC``, and §7
    Business Constraints states plainly *"Append-only"* -- the same
    unambiguous, spec-declared append-only classification every sibling
    ``*_history`` table in this codebase carries. ``InvoiceHistory``
    therefore gets ``created_at`` / ``created_by`` only -- no
    ``updated_at`` / ``updated_by`` / ``version``, and consequently no
    ``__mapper_args__ = {"version_id_col": ...}``.
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


class InvoiceHistory(Base, AppendOnlyAuditColumns):
    """``H4 (ERD id) — invoice_history`` — immutable invoice state-change log (Classification: H)."""

    __tablename__ = "invoice_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------------- invoice_id
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "invoice.id",
            name=fk_index_name("invoice_history", "invoice_id", "invoice"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- actor_user_id
    # This table's own spec'd business actor -- distinct from AAC's mixin
    # created_by. NOT NULL per spec (unlike several sibling history
    # tables' own nullable actor columns). See module docstring's
    # dedicated section.
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("invoice_history", "actor_user_id", "app_user"),
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
    # sqlalchemy.Text() -- same unbounded-text treatment as every other
    # *_history.note column. Nullable, no conditional CHECK per spec.
    note: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: InvoiceState vocabulary on BOTH from_state and to_state,
        # in ONE combined constraint -- transcribed verbatim from
        # invoice.py's own ck_invoice_state CHECK text. See module
        # docstring's dedicated section.
        CheckConstraint(
            "from_state IN ("
            "'DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'PAID', "
            "'CLOSED_CORRECTED', 'VOID'"
            ") AND to_state IN ("
            "'DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'PAID', "
            "'CLOSED_CORRECTED', 'VOID'"
            ")",
            name=ck_index_name("invoice_history", "states"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("invoice_history", "invoice_id"),
            "invoice_id",
        ),
        # Composite index -- (invoice_id, event_at), ordinary composite
        # case.
        Index(
            idx_index_name(
                "invoice_history",
                composite_descriptor(("invoice_id", "event_at")),
            ),
            "invoice_id",
            "event_at",
        ),
    )


__all__ = ["InvoiceHistory"]
