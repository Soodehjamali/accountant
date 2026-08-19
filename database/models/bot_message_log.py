"""``H5 (ERD id) — bot_message_log`` ORM model (immutable log of messenger-bot conversation traffic).

Authority: ``07_DATABASE_SPEC.md`` §H5 (spec's own section header:
``H5 (ERD id) — bot_message_log``; distinct from the *other* ``H5`` label
already used elsewhere in this codebase for ``order_status_history``
(``H5 (ERD: T12) — order_status_history``) -- the same "spec reuses a bare
``H`` prefix twice, once per its own ERD-id-less numbering track and once
for a table with a real ERD numeric id" situation ``invoice_history`` (H4,
this same change) already documents, and the same collision this task's
own prompt explicitly flags, confirmed directly against the live spec text
fetched for this change rather than assumed) -- this table **does** have a
full detailed spec section, so the spec is primary authority here;
``06_ERD.md`` (F.9 — Notifications & Bot Messaging) is
secondary/corroborating only::

    H5 (ERD id) — bot_message_log
    Purpose: Immutable log of bot conversation traffic for audit (BR-B3,
        SRS E31).
    PK: id (UUID)
    FK: bot_session_id -> bot_session.id; bot_platform_id ->
        bot_platform_ref.id
    Column Definitions: +AAC; bot_session_id UUID NOT NULL; bot_platform_id
        UUID NOT NULL; direction VARCHAR(10) NOT NULL; raw_payload JSONB
        NOT NULL; command_parsed VARCHAR(120) NULL; occurred_at
        TIMESTAMPTZ NOT NULL DEFAULT now()
    Unique Constraints: none — chronological append
    Check Constraints: ck_bot_message_log_direction (direction IN
        ('INBOUND','OUTBOUND'))
    Business Constraints: Append-only; never edited or deleted, including
        after session revocation.
    Recommended Indexes: btree on bot_session_id
    Composite Indexes: (bot_session_id, occurred_at)
    Partial Indexes: none
    Partitioning Strategy: Range partition by occurred_at (monthly) —
        highest-cardinality, lowest-value table candidate per PART M;
        shortest retention policy recommended.
    Soft Delete Strategy: None
    Audit Strategy: Self-auditing

Both ``bot_session`` (already present in this codebase) and
``bot_platform_ref`` (already present) are referenced, not owned in the
line-item-child sense -- this is a pure event log keyed off a session and a
platform, the same "log table referencing its subject, not a child of a
single aggregate root" shape ``audit_log`` (already present) has relative
to its own polymorphic entity reference.

Non-reserved-word FK targets -- ``bot_session_id -> bot_session.id`` /
``bot_platform_id -> bot_platform_ref.id``:
    Both are ordinary identifiers, no quoting concerns for either FK.

``direction`` -- ``VARCHAR(10)``, placeholder width, narrower than any
exact-fit factory:
    No ``database/types.py`` factory produces exactly 10 characters -- the
    narrowest available is ``state_token_type()`` at ``VARCHAR(16)``. This
    is the same "borrow the closest available factory, not an exact-width
    match" placeholder treatment ``invoice.state`` /
    ``invoice_history.from_state``/``to_state`` (this same change,
    ``state_token_long_type()`` for a spec'd ``VARCHAR(20)``) already
    receive, applied here at the opposite end of the width scale (the
    factory is *wider* than the spec's own column, not narrower -- the
    spec's 2-value vocabulary (``INBOUND``/``OUTBOUND``) comfortably fits
    within the wider ``VARCHAR(16)`` without truncation risk, so no data
    can be lost by the substitution).

``command_parsed`` -- ``token_type()``, exact width match:
    ``VARCHAR(120)`` per spec -- ``database.types.token_type()`` produces
    precisely this width. Nullable per spec (not every inbound message
    resolves to a recognized command/intent).

``raw_payload`` -- ``JSONB``, same treatment as ``notification.payload``:
    Declared via ``sqlalchemy.dialects.postgresql.JSONB`` directly, the
    same "consume the concrete dialect type directly, no
    ``database/types.py`` factory exists for JSON" treatment every other
    JSONB column in this codebase already establishes
    (``report_definition.parameters``, ``notification.payload``,
    ``order_price_freeze.precedence_chain_json``,
    ``report_snapshot.snapshot_data``). Typed as plain ``Mapped[dict]`` --
    matching ``notification.payload``'s own annotation exactly (both hold
    a single platform message's full structured payload, the same "one
    object, not a list of records" shape), rather than
    ``order_price_freeze``'s own ``list[dict[str, Any]]`` annotation for
    its differently-shaped ordered-list JSONB column. ``NOT NULL`` per
    spec, no default -- every logged message carries a real payload at
    insert time.

``occurred_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment every other AAC-adjacent
    posting-timestamp column in this codebase receives.

No separate business-actor column:
    Unlike ``invoice_history.actor_user_id`` /
    ``notification_history.actor_user_id``, this table's own §3 Foreign
    Keys bullet list names only ``bot_session_id`` and ``bot_platform_id``
    -- no third, business-actor FK to ``app_user.id`` is spec'd. Spec §13's
    own Audit Strategy line -- *"Self-auditing"* -- and the absence of any
    actor-shaped column in §4's Column Definitions table confirm this: a
    bot message's "actor" is the remote messenger-platform user on the
    other end of ``bot_session``, not an internal ``app_user`` row, so
    there is no meaningful internal actor to record beyond AAC's own
    mixin-supplied ``created_by`` (which would typically be NULL/system
    here, since the message itself originates outside the application).
    The same "no separate business actor column" shape
    ``order_price_freeze.py`` / ``report_snapshot.py`` (both this
    codebase's own AAC tables with §13: *"created_by (AAC)"* and nothing
    more) already have.

No ``UniqueConstraint`` -- explicit per spec:
    Spec §5: *"none — chronological append"* -- the same affirmative-absence
    treatment every sibling ``*_history``/``*_log`` table in this codebase
    already documents.

Indexes:
    Recommended single-column ``idx_bot_message_log_bot_session_id`` on
    ``bot_session_id`` (spec §8) via ``idx_index_name``, plus a composite
    ``(bot_session_id, occurred_at)`` index (spec §9) via ``idx_index_name``
    + ``composite_descriptor`` -- an ordinary composite case, the spec
    gives no literal name override for it. No partial index (spec §10:
    none).

Out of scope for this model (not implemented here):
    * Range partitioning by ``occurred_at`` (monthly) with a short
      retention policy -- spec §11 marks this a physical-design/
      migration-time decision (this is explicitly flagged by the spec as
      the single highest-volume table candidate in the entire schema at
      scale, making retention tooling important, but that tooling is
      operational/migration-layer, not an ORM column/constraint concern).
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    The spec's own §4 Column Definitions table opens with ``+AAC``, and §7
    Business Constraints states plainly *"Append-only; never edited or
    deleted, including after session revocation"* -- an unambiguous,
    spec-declared append-only classification. ``BotMessageLog`` therefore
    gets ``created_at`` / ``created_by`` only.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import state_token_type, token_type


class BotMessageLog(Base, AppendOnlyAuditColumns):
    """``H5 (ERD id) — bot_message_log`` — immutable log of messenger-bot conversation traffic (Classification: H)."""

    __tablename__ = "bot_message_log"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- bot_session_id
    bot_session_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "bot_session.id",
            name=fk_index_name("bot_message_log", "bot_session_id", "bot_session"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- bot_platform_id
    bot_platform_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "bot_platform_ref.id",
            name=fk_index_name("bot_message_log", "bot_platform_id", "bot_platform_ref"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------- direction
    # Placeholder width -- see module docstring's dedicated section
    # (VARCHAR(10) per spec; state_token_type() is the narrowest available
    # factory at VARCHAR(16), which comfortably fits the 2-value
    # vocabulary with no truncation risk).
    direction: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- raw_payload
    # Same JSONB/Mapped[dict] treatment as notification.payload -- one
    # object, not a list of records. See module docstring's dedicated
    # section.
    raw_payload: Mapped[dict] = mapped_column(
        JSONB(),
        nullable=False,
    )

    # ----------------------------------------------------------- command_parsed
    # Exact width match via token_type(). Nullable per spec.
    command_parsed: Mapped[str | None] = mapped_column(
        token_type(),
        nullable=True,
    )

    # -------------------------------------------------------------- occurred_at
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # CHECK: 2-value direction vocabulary.
        CheckConstraint(
            "direction IN ('INBOUND', 'OUTBOUND')",
            name=ck_index_name("bot_message_log", "direction"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("bot_message_log", "bot_session_id"),
            "bot_session_id",
        ),
        # Composite index -- (bot_session_id, occurred_at), ordinary
        # composite case.
        Index(
            idx_index_name(
                "bot_message_log",
                composite_descriptor(("bot_session_id", "occurred_at")),
            ),
            "bot_session_id",
            "occurred_at",
        ),
    )


__all__ = ["BotMessageLog"]
