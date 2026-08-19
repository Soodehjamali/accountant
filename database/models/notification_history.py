"""``H8 (ERD id) — notification_history`` ORM model (immutable notification state-transition log).

Authority: ``07_DATABASE_SPEC.md`` §H8 (spec's own section header:
``H8 (ERD id) — notification_history``; the ERD does not assign this table
its own numeric ``T``/``H`` id, hence "ERD id" in the heading, the same
convention already applied to ``invoice_history`` (H4), ``bot_message_log``
(H5), ``audit_log`` (H6), and ``approval_history`` (H7)) -- this table
**does** have a full detailed spec section, so the spec is primary
authority here; ``06_ERD.md`` (F.15 — Notification History) is
secondary/corroborating only::

    H8 (ERD id) — notification_history
    Purpose: Immutable log of state transitions on a notification,
        including retries.
    PK: id (UUID)
    FK: notification_id -> notification.id; actor_user_id -> app_user.id
        (nullable)
    Column Definitions: +AAC; notification_id UUID NOT NULL; actor_user_id
        UUID NULL (nullable, NULL for automated system retries); from_state
        VARCHAR(16) NOT NULL; to_state VARCHAR(16) NOT NULL; event_at
        TIMESTAMPTZ NOT NULL DEFAULT now(); retry_attempt SMALLINT NOT NULL
        DEFAULT 0; error_detail TEXT NULL (populated on FAILED transitions)
    Unique Constraints: none — chronological append
    Check Constraints: ck_notification_history_states (from_state IN
        (...NotificationState...) AND to_state IN (...NotificationState...))
    Business Constraints: Append-only
    Recommended Indexes: btree on notification_id
    Composite Indexes: (notification_id, event_at)
    Partial Indexes: none
    Partitioning Strategy: Range partition by event_at (monthly) — tracks
        notification volume, very high.
    Soft Delete Strategy: None
    Audit Strategy: Self-auditing
    Notes: —

Owned by ``notification`` (T24, already present in this codebase) via
``notification_id`` — the same "immutable state-log child of a mutable UAC
header" relationship ``order_status_history`` has to ``order`` and
``transfer_history`` has to ``stock_transfer``.

Non-reserved-word FK targets -- ``notification_id -> notification.id`` /
``actor_user_id -> app_user.id``:
    Both are ordinary identifiers, no quoting concerns for either FK.

Column-style note -- this model uses the codebase's ``types.py``/``_SAUuid``/
``sa_text`` idiom, NOT ``notification.py``'s own raw ``String``/``Uuid``/
``text`` idiom:
    The already-built ``database/models/notification.py`` (this table's
    own parent) declares its ``channel``/``state`` columns as bare
    ``String(16)`` and imports plain ``sqlalchemy.Uuid``/``sqlalchemy.text``
    rather than routing through ``database/types.py``'s
    ``state_token_type()`` factory or this codebase's usual ``_SAUuid``/
    ``sa_text`` aliasing convention -- an isolated stylistic divergence
    from a different authoring pass, not a project-wide rule change (every
    other table built in this same lineage --``shipment``, ``invoice``,
    ``payment``, ``report_definition``, all of which this model's own
    style directly follows -- uses the ``types.py`` factories and
    ``_SAUuid``/``sa_text`` aliases throughout). Since ``state_token_type()``
    produces the exact same ``VARCHAR(16)`` column ``notification.py``'s
    own bare ``String(16)`` does, the two are wire-format-identical; this
    model follows the majority convention (``types.py`` factories,
    ``_SAUuid``, ``sa_text``) for internal consistency with the rest of
    this codebase's tables rather than pattern-matching its one immediate
    parent.

``actor_user_id`` -- nullable, distinct from AAC's own ``created_by``:
    This table's own spec'd business actor ("who caused this specific
    notification-state transition"), nullable per spec ("NULL for
    automated system retries" -- e.g. a scheduled retry job with no human
    actor). The same "business column vs. mixin audit column, same target
    table, different semantic role" situation ``shipment_status_history
    .actor_user_id`` / ``transfer_history.actor_user_id`` already document.

``from_state`` / ``to_state`` -- ``VARCHAR(16)``, EXACT spec match via
``state_token_type()``:
    Both columns are spec'd exactly ``VARCHAR(16)`` -- precisely
    ``state_token_type()``'s own width, no placeholder needed. Unlike
    ``shipment_status_history`` (whose own ``from_state`` is nullable and
    whose CHECK deliberately covers only ``to_state``), this table's spec
    marks **both** ``from_state`` and ``to_state`` ``NOT NULL`` (a
    notification always has a real prior state to transition from --
    ``notification.state`` itself defaults to ``'QUEUED'`` at creation, so
    there is no "inaugural NULL" case the way ``shipment_status_history``'s
    "NULL only on the CREATE row" note describes), and the spec's own §6
    CHECK text combines both columns in one constraint (``from_state IN
    (...) AND to_state IN (...)``) -- the same combined-check treatment
    ``transfer_history`` / ``order_status_history`` already use, NOT
    ``shipment_status_history``'s asymmetric to-state-only treatment.

``NotificationState`` vocabulary -- transcribed verbatim from
``notification.py``'s own CHECK:
    ``ck_notification_history_states`` bounds both ``from_state`` and
    ``to_state`` to the same 4-value vocabulary
    ``notification.py``'s own ``ck_notification_state`` CHECK already
    enforces on ``notification.state``
    (``'QUEUED','SENT','FAILED','ACKNOWLEDGED'``) -- transcribed verbatim
    (same value list, same order) from that model's own CHECK text rather
    than retyped independently, to guarantee the two CHECKs can never
    silently drift apart. The same treatment
    ``transfer_history.ck_transfer_history_states`` already gives relative
    to ``stock_transfer.ck_stock_transfer_state``.

``retry_attempt`` -- ``SMALLINT``, plain ``sqlalchemy.SmallInteger``:
    No ``database/types.py`` factory exists for ``SMALLINT`` (its scope is
    limited to ``NumericPrecision``/``StringLength`` members per that
    module's own docstring) -- the same "consume the concrete SQLAlchemy
    type directly when no factory abstraction yet exists for it" treatment
    ``notification.py``'s own ``retry_count`` column, and this same
    module's ``UniversalAuditColumns.version`` column
    (``sqlalchemy.Integer`` directly), already receive. ``NOT NULL DEFAULT
    0`` via the dual ``default=0``/``server_default=sa_text("0")`` pattern.

``error_detail`` -- ``sqlalchemy.Text()``, same unbounded-text treatment as
every other ``*_history.note`` column in this codebase:
    The spec's own column type is literally ``TEXT`` (unbounded), not a
    ``VARCHAR(N)``. Nullable per spec ("Populated on FAILED transitions" --
    a conditional-population business rule the spec does NOT back with a
    CHECK constraint here, unlike ``shipment_status_history``'s own
    ``to_state <> 'FAILED' OR note IS NOT NULL`` rule -- this table's own
    §6 CHECK text names only the states CHECK, nothing conditioning
    ``error_detail`` on ``to_state``, so no such CHECK is added here
    either; flagged so a future edit doesn't "harmonize" this with
    ``shipment_status_history``'s stricter, spec-distinct rule).

``event_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment every other ``*_history
    .event_at`` column in this codebase receives.

No ``UniqueConstraint`` -- explicit per spec:
    Spec §5: *"none — chronological append"* -- the same affirmative-absence
    treatment ``transfer_history`` / ``order_status_history`` /
    ``shipment_status_history`` already document. No ``UniqueConstraint``
    is declared.

Indexes:
    Recommended single-column ``idx_notification_history_notification_id``
    on ``notification_id`` (spec §8) via ``idx_index_name``, plus a
    composite ``(notification_id, event_at)`` index (spec §9) via
    ``idx_index_name`` + ``composite_descriptor`` -- an ordinary composite
    case, the spec gives no literal name override for it. No partial index
    (spec §10: none).

Out of scope for this model (not implemented here):
    * Range partitioning by ``event_at`` (monthly) -- spec §11 marks this a
      physical-design/migration-time decision.
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    The spec's own §4 Column Definitions table opens with ``+AAC``, and §7
    Business Constraints states plainly *"Append-only"* -- the same
    unambiguous, spec-declared append-only classification
    ``transfer_history`` / ``order_status_history`` /
    ``shipment_status_history`` all carry. ``NotificationHistory`` therefore
    gets ``created_at`` / ``created_by`` only -- no ``updated_at`` /
    ``updated_by`` / ``version``, and consequently no ``__mapper_args__ =
    {"version_id_col": ...}``.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, Text
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import state_token_type


class NotificationHistory(Base, AppendOnlyAuditColumns):
    """``H8 (ERD id) — notification_history`` — immutable notification state-transition log (Classification: H)."""

    __tablename__ = "notification_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------- notification_id
    notification_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "notification.id",
            name=fk_index_name("notification_history", "notification_id", "notification"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- actor_user_id
    # This table's own spec'd business actor -- distinct from AAC's mixin
    # created_by. Nullable: spec "NULL for automated system retries". See
    # module docstring's dedicated section.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("notification_history", "actor_user_id", "app_user"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- from_state
    # VARCHAR(16), EXACT spec match via state_token_type(). NOT NULL --
    # unlike shipment_status_history, there is no "inaugural NULL" case
    # here. See module docstring's dedicated section.
    from_state: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ---------------------------------------------------------------- to_state
    to_state: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------------- event_at
    event_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ------------------------------------------------------------- retry_attempt
    # Plain SmallInteger -- no database/types.py factory exists for
    # SMALLINT. See module docstring's dedicated section.
    retry_attempt: Mapped[int] = mapped_column(
        SmallInteger(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------- error_detail
    # sqlalchemy.Text() -- same unbounded-text treatment as every other
    # *_history.note column. Nullable; populated on FAILED transitions
    # (application-layer convention, no CHECK -- see module docstring).
    error_detail: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: NotificationState vocabulary on BOTH from_state and
        # to_state, in ONE combined constraint -- transcribed verbatim from
        # notification.py's own ck_notification_state CHECK text. See
        # module docstring's dedicated section.
        CheckConstraint(
            "from_state IN ('QUEUED', 'SENT', 'FAILED', 'ACKNOWLEDGED') "
            "AND to_state IN ('QUEUED', 'SENT', 'FAILED', 'ACKNOWLEDGED')",
            name=ck_index_name("notification_history", "states"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("notification_history", "notification_id"),
            "notification_id",
        ),
        # Composite index -- (notification_id, event_at), ordinary
        # composite case.
        Index(
            idx_index_name(
                "notification_history",
                composite_descriptor(("notification_id", "event_at")),
            ),
            "notification_id",
            "event_at",
        ),
    )


__all__ = ["NotificationHistory"]
