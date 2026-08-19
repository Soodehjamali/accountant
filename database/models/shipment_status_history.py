"""``H7 (ERD: T16) — shipment_status_history`` ORM model (immutable shipment state log).

Authority: ``07_DATABASE_SPEC.md`` §H7 (labeled ``H7 (ERD: T16)`` in the
spec's own section header) -- this table **does** have a full detailed spec
section, so the spec is primary authority here, the same "spec wins when it
has a dedicated section" convention already applied to ``transfer_history``
(H4 / ERD T6) and ``order_status_history`` (H5 / ERD T12). ``06_ERD.md``
(F.5 — Fulfillment / Shipping, T16 line) is secondary/corroborating only --
both labels name the same table; the spec's own section header
cross-references the ERD code explicitly (``H7 (ERD: T16)``), so this is not
a conflict, just two numbering schemes for one table::

    H7 (ERD: T16) — shipment_status_history
    Purpose: Immutable shipment state log, including geo-tracking for
        factory-direct delivery.
    PK: id (UUID)
    FK: shipment_id -> shipment.id; actor_user_id -> app_user.id
    Column Definitions: +AAC; shipment_id UUID NOT NULL; actor_user_id UUID
        NULL (nullable for automated tracking pings); from_state VARCHAR(16)
        NULL (NULL only on the inaugural CREATE row); to_state VARCHAR(16)
        NOT NULL; event_at TIMESTAMPTZ NOT NULL DEFAULT now(); lat
        NUMERIC(9,6) NULL (geo-tracking latitude); lng NUMERIC(9,6) NULL
        (geo-tracking longitude); note TEXT NULL (mandatory when to_state =
        'FAILED')
    Unique Constraints: none — chronological append
    Check Constraints: ck_shipment_status_history_states (to_state IN
        ('CREATED','PICKING','PACKED','DISPATCHED','IN_TRANSIT','DELIVERED',
        'FAILED')); ck_shipment_status_history_lat (lat IS NULL OR lat
        BETWEEN -90 AND 90); ck_shipment_status_history_lng (lng IS NULL OR
        lng BETWEEN -180 AND 180); ck_shipment_status_history_failed_note
        (to_state <> 'FAILED' OR note IS NOT NULL)
    Business Constraints: Append-only; exactly one CREATE-equivalent row
        (from_state IS NULL) per shipment; FAILED triggers an exception
        workflow (application-orchestrated, e.g. auto-creating an
        approval_request or notification).
    Recommended Indexes: btree on shipment_id
    Composite Indexes: (shipment_id, event_at)
    Partial Indexes: none
    Partitioning Strategy: Range partition by event_at (monthly) -- tracks
        shipment volume.
    Soft Delete Strategy: None
    Audit Strategy: Self-auditing

Owned by the Shipment aggregate (``06_ERD.md``: *"Shipment (root: shipment)
-- owns shipment_line, shipment_status_history"*) via ``shipment_id``, the
same aggregate ``shipment_line.py`` (T15) belongs to.

Both FKs are real from the outset:
    ``shipment`` (T14, this same change) and ``app_user`` both exist, so
    ``shipment_id`` and ``actor_user_id`` are declared as real
    ``ForeignKey()`` constraints from the start -- no deferred-FK section to
    write for this table.

Non-reserved-word FK target -- ``shipment_id -> shipment.id``:
    ``shipment`` is an ordinary identifier -- no quoting concern applies
    here.

``actor_user_id`` -- distinct from AAC's own ``created_by``:
    Both are real, nullable FKs to ``app_user.id``, but they are not a
    redundant pair: ``actor_user_id`` is this table's own spec'd business
    column ("who caused this specific state transition" -- the very actor
    the row exists to record, spec: *"nullable for automated tracking
    pings"*), while AAC's ``created_by`` is the generic append-only
    audit-trail actor supplied by the mixin on every AAC-using table. This
    is the exact same "business column vs. mixin audit column, same target
    table, different semantic role" situation
    ``transfer_history.actor_user_id`` / ``order_status_history.actor_user_id``
    already document.

``from_state`` / ``to_state`` -- ``VARCHAR(16)``, EXACT spec match via
``state_token_type()``:
    Unlike ``transfer_history.from_state``/``to_state`` (which had to fall
    back to the placeholder ``state_token_long_type()`` -> ``VARCHAR(24)``
    because no exact-width factory existed for that table's spec'd
    ``VARCHAR(20)``), this table's spec width is exactly ``VARCHAR(16)`` --
    precisely ``state_token_type()``'s own width. No placeholder needed for
    either column. ``from_state`` is nullable (NULL only on the inaugural
    CREATE row per spec); ``to_state`` is ``NOT NULL``.

``lat`` / ``lng`` -- ``geo_type()``, exact spec match:
    ``NUMERIC(9, 6)`` per spec -- ``database.types.geo_type()``'s own
    docstring names ``shipment_status_history.lat`` /
    ``shipment_status_history.lng`` explicitly as its intended (and, at time
    of writing, only) consumer. Both nullable; their range is enforced via
    the two dedicated CHECK constraints below, not at the type level.

``note`` -- ``sqlalchemy.Text()``, same unbounded-text treatment as
``order_status_history.note`` / ``transfer_history.note``:
    The spec's own column type is literally ``TEXT`` (unbounded), not a
    ``VARCHAR(N)`` -- no ``database/types.py`` factory exists for an
    unbounded text column, so this model uses ``sqlalchemy.Text()``
    directly, the same treatment this codebase's other ``*_history.note``
    columns already established. Nullable at the column level; the "NOT
    NULL when to_state = 'FAILED'" rule is enforced via
    ``ck_shipment_status_history_failed_note`` below, not via
    column-level ``nullable=False``.

``event_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment ``transfer_history.event_at`` /
    ``order_status_history.event_at`` already receive.

No ``UniqueConstraint`` -- explicit per spec:
    Spec §5 states plainly *"none — chronological append"* -- the same
    affirmative-absence treatment ``transfer_history`` /
    ``order_status_history`` already document for their own identical
    unique-constraints line. No ``UniqueConstraint`` is declared.

CHECK constraints -- four, transcribed verbatim from the spec:
    ``ck_shipment_status_history_states`` bounds **only** ``to_state`` --
    deliberately different from ``transfer_history``'s /
    ``order_status_history``'s own combined "``from_state IN (...) AND
    to_state IN (...)``" CHECKs, because this table's ``from_state`` is
    nullable (NULL on the inaugural CREATE row) and the spec's own §6 gives
    the constraint text as ``to_state IN (...)`` only, with no ``from_state``
    clause. This is a deliberate, spec-driven divergence from the sibling
    history tables' pattern, not an oversight -- flagged explicitly so a
    future edit doesn't "harmonize" it by adding a ``from_state IN (...)``
    clause the spec never asked for (doing so would also require handling
    the ``from_state IS NULL`` case, which the spec's own inaugural-row rule
    already covers via nullability, not via an IN-list clause).
    ``ck_shipment_status_history_lat`` / ``_lng`` bound the two geo columns
    to valid latitude/longitude ranges, each written as ``<col> IS NULL OR
    <col> BETWEEN ...`` so a NULL (no geo-ping on this event) always passes.
    ``ck_shipment_status_history_failed_note`` enforces the spec's own
    business rule ("Mandatory when to_state = 'FAILED'") as a CHECK rather
    than at the column-nullability level, since the requirement is
    conditional on another column's value.

Indexes:
    Recommended single-column ``idx_shipment_status_history_shipment_id`` on
    ``shipment_id`` (spec §8) via ``idx_index_name``, plus a composite
    ``(shipment_id, event_at)`` index (spec §9) via ``idx_index_name`` +
    ``composite_descriptor`` -- an ordinary composite case, the spec gives no
    literal name override for it, so the standard helper output is used
    as-is. No partial index (spec §10: none).

Out of scope for this model (not implemented here):
    * The "exactly one CREATE-equivalent row per shipment" business
      constraint -- a cross-row invariant the spec itself does not attach a
      CHECK/partial-unique-index mechanism to (contrast with
      ``approval_request``'s explicit partial-unique-index "one PENDING
      request" rule); left as an application-layer concern per the spec's
      own phrasing.
    * The FAILED-triggers-exception-workflow business constraint --
      spec-flagged application-orchestrated (auto-creating an
      ``approval_request`` / ``notification``), not a schema-level concern.
    * Range partitioning by ``event_at`` (monthly) -- spec §11 marks this a
      physical-design/migration-time decision, the same treatment every
      other table's own partitioning-strategy note in this codebase already
      receives.
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    Classification ``H`` (spec's own header: ``H7``), Business Constraints
    §7: *"Append-only"* -- the same unqualified append-only classification
    ``transfer_history`` (H4) / ``order_status_history`` (H5) already carry,
    both of which also use AAC. ``ShipmentStatusHistory`` therefore gets
    ``created_at`` / ``created_by`` only -- no ``updated_at`` /
    ``updated_by`` / ``version``, and consequently no ``__mapper_args__ =
    {"version_id_col": ...}`` -- a state-transition/geo-tracking log row is
    complete and final the instant it is inserted.

Naming convention:
    Both FKs use ``fk_index_name`` normally
    (``fk_shipment_status_history_shipment_id_shipment_id``,
    ``fk_shipment_status_history_actor_user_id_app_user_id``). All four
    CHECKs use ``ck_index_name`` normally -- the standard helper output
    already matches the spec's four literal names verbatim
    (``ck_shipment_status_history_states``, ``..._lat``, ``..._lng``,
    ``..._failed_note``) -- no override needed. The recommended index and
    composite index both use ``idx_index_name`` (the latter with
    ``composite_descriptor``) -- no literal override needed for either.
    There is no ``UniqueConstraint`` -- see the section above.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import geo_type, state_token_type


class ShipmentStatusHistory(Base, AppendOnlyAuditColumns):
    """``H7 (ERD: T16) — shipment_status_history`` — immutable shipment state log, incl. geo-tracking (Classification: H)."""

    __tablename__ = "shipment_status_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- shipment_id
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "shipment.id",
            name=fk_index_name("shipment_status_history", "shipment_id", "shipment"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- actor_user_id
    # This table's own spec'd business actor -- distinct from AAC's mixin
    # created_by. Nullable: spec "nullable for automated tracking pings".
    # See module docstring's dedicated section.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("shipment_status_history", "actor_user_id", "app_user"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- from_state
    # Nullable -- NULL only on the inaugural CREATE row. VARCHAR(16), EXACT
    # spec match via state_token_type() (no placeholder needed).
    from_state: Mapped[str | None] = mapped_column(
        state_token_type(),
        nullable=True,
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

    # -------------------------------------------------------------------- lat
    # Geo-tracking latitude. Range enforced via
    # ck_shipment_status_history_lat below, not at the type level.
    lat: Mapped[decimal.Decimal | None] = mapped_column(
        geo_type(),
        nullable=True,
    )

    # -------------------------------------------------------------------- lng
    # Geo-tracking longitude. Range enforced via
    # ck_shipment_status_history_lng below.
    lng: Mapped[decimal.Decimal | None] = mapped_column(
        geo_type(),
        nullable=True,
    )

    # -------------------------------------------------------------------- note
    # sqlalchemy.Text() -- same unbounded-text treatment as
    # order_status_history.note / transfer_history.note. Nullable at the
    # column level; "mandatory when to_state='FAILED'" is enforced via
    # ck_shipment_status_history_failed_note below.
    note: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: to_state vocabulary ONLY -- deliberately does NOT also
        # check from_state (which may be NULL on the inaugural row), unlike
        # transfer_history/order_status_history's own combined checks. See
        # module docstring's dedicated section on this divergence.
        CheckConstraint(
            "to_state IN ("
            "'CREATED', 'PICKING', 'PACKED', 'DISPATCHED', 'IN_TRANSIT', "
            "'DELIVERED', 'FAILED'"
            ")",
            name=ck_index_name("shipment_status_history", "states"),
        ),
        # CHECK: latitude range, NULL-safe.
        CheckConstraint(
            "lat IS NULL OR lat BETWEEN -90 AND 90",
            name=ck_index_name("shipment_status_history", "lat"),
        ),
        # CHECK: longitude range, NULL-safe.
        CheckConstraint(
            "lng IS NULL OR lng BETWEEN -180 AND 180",
            name=ck_index_name("shipment_status_history", "lng"),
        ),
        # CHECK: note is mandatory when to_state = 'FAILED'.
        CheckConstraint(
            "to_state <> 'FAILED' OR note IS NOT NULL",
            name=ck_index_name("shipment_status_history", "failed_note"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("shipment_status_history", "shipment_id"),
            "shipment_id",
        ),
        # Composite index -- (shipment_id, event_at), ordinary composite
        # case.
        Index(
            idx_index_name(
                "shipment_status_history",
                composite_descriptor(("shipment_id", "event_at")),
            ),
            "shipment_id",
            "event_at",
        ),
    )


__all__ = ["ShipmentStatusHistory"]
