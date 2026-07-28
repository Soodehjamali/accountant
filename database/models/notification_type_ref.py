"""``R9 — notification_type_ref`` ORM model (notification template/type catalog).

Authority: docs/06_ERD.md, PART B → ``R9 — notification_type_ref``::

    R9 — notification_type_ref
    Purpose: Catalog of notification templates/types.
    PK: id | code unique
    Classification: R

This entity has **no entry in docs/07_DATABASE_SPEC.md** — PART B reference
tables were not ported into the physical spec; the ERD is the source of truth
for ``notification_type_ref``'s own columns (``notification_type_id`` appears
as an FK target on the ``notification`` table — T24 — in the spec).

Field surface (deliberately minimal):
    Unlike most sibling R-class reference tables, the ERD's R9 entry lists
    **no "Important fields"** line — its only concrete fields are ``id`` and
    ``code`` (unique). Compare R2 (``code``/``name``/``class``), R4
    (``code``/``sign``/``label``), R11 (``code``/``scope``/``label``), each of
    which an ERD "Important fields" enumerates; R9 carries none beyond the PK
    and the unique ``code``. The model therefore declares **only** ``id`` and
    ``code`` — no ``name`` / ``label`` / ``description`` is invented (the ERD
    is the source of truth and lists none). If a future spec revision adds a
    human-readable label / template body / channel-default column to R9, it is
    added then; not preemptively.

Relationship to the PART A NotificationChannel / NotificationState enums:
    The PART A enums are ``NotificationChannel = {IN_APP, EMAIL, BOT_PUSH,
    SMS}`` and ``NotificationState = {QUEUED, SENT, FAILED, ACKNOWLEDGED}``.
    Per PART A's own preamble, enums that need runtime editability are
    promoted to a ``*_type_ref`` reference table; however, the ERD's R9 entry
    does **not** state that ``notification_type_ref`` carries a ``channel`` or
    ``state`` column. Those enum-backed columns live on the **consumer**
    table — T24 ``notification`` — whose ERD entry explicitly lists
    ``channel (NotificationChannel)`` and ``state (NotificationState)`` as its
    own columns. R9's own field list declares no bounded-set column, so —
    following the same discipline as ``city_ref`` (which added no CHECK
    because the ERD implied no bounded set for its R-table fields) — **no
    CHECK constraint is added here**. The PART A NotificationChannel /
    NotificationState bounded sets are enforced on T24 ``notification``, not
    on this reference catalog.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` / ``version``.
    The ERD's §0.2 Governing Design Decisions states audit fields are stored by
    *every* table, so R-class editable reference tables adopt UAC. A
    notification-type catalog is runtime-editable (operators add/retire
    notification templates/types as business processes evolve), so it gets
    UAC. Like ``currency`` / ``reason_code_ref`` / ``movement_type_ref`` /
    ``city_ref`` / ``unit_of_measure``, it deliberately carries **no**
    ``deleted_at`` / soft-delete: the ERD does not list a soft-delete column
    for R9, and a reference catalog is retired by discontinuing use, not
    soft-deleted (``UniversalAuditColumns`` as defined in ``database.mixins``
    already carries no ``deleted_at``, so the mixin is used as-is).

Optimistic locking:
    ``__mapper_args__ = {"version_id_col": "version"}`` opts the model into the
    UAC ``version`` column as the SQLAlchemy row-version concurrency token (per
    ``database.mixins``'s documented opt-in mechanism — the mixin supplies the
    column, the model wires the mapper). Same pattern as the other R-class
    reference models.

Naming convention:
    The column-level ``unique=True`` on ``code`` auto-named via the shared
    ``MetaData`` naming convention to ``uq_notification_type_ref_code``
    (``uq_%(table_name)s_%(column_0_name)s``). There is **no** CHECK constraint
    (see "Relationship to the PART A enums" above — R9 implies no bounded set),
    so no ``ck_*`` name is generated. No explicit operational index is authored
    for this minimal catalog.

Column-type choices (prefer existing ``database.types`` helpers over raw
``String(N)`` literals — no length invented):

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``. A notification-type code
  is a short controlled-vocabulary token identifying a template/type (realistic
  longest values e.g. ``COMMISSION_CLAWBACK_NOTICE`` = 25, ``APPROVAL_REQUIRED``
  = 18, ``SHIPMENT_DISPATCHED`` = 18, ``LOW_STOCK_ALERT`` = 15). The helper's
  documented purpose is "SKU / warehouse code / currency ISO-3 / short
  codes"; ``StringLength.CODE_SHORT`` (40) is the existing, non-invented,
  authoritative fit — the 40-char budget comfortably holds every realistic
  notification-type tag without being tight and without inventing a new
  ``StringLength`` member. Same choice as ``reason_code_ref.code`` /
  ``movement_type_ref.code`` / ``city_ref.code`` / ``unit_of_measure.code``.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import code_short_type


class NotificationTypeRef(Base, UniversalAuditColumns):
    """``R9 — notification_type_ref`` — notification template/type catalog (Classification: R)."""

    __tablename__ = "notification_type_ref"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token (mixins.py opt-in mechanism).
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- code
    # Short controlled-vocabulary code identifying a notification template/type
    # (e.g. ORDER_SHIPPED, APPROVAL_REQUIRED, COMMISSION_CLAWBACK_NOTICE).
    # ``code_short_type`` (VARCHAR(40)) is the existing, non-invented helper
    # whose documented purpose is short codes; the 40-char budget comfortably
    # holds every realistic notification-type tag (longest ~25) without being
    # tight and without inventing a new ``StringLength`` member.
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # No CHECK constraints: the ERD's own R9 field list declares no bounded-
    # value column. The PART A NotificationChannel / NotificationState enums
    # belong to the consumer table (T24 ``notification``), not to this
    # reference catalog — see the module docstring. None invented (same
    # discipline as ``city_ref``).

    # No ``__table_args__``: no CHECK constraints, no explicit operational
    # indexes for this minimal catalog. The sole unique constraint is the
    # column-level ``unique=True`` on ``code`` (auto-named). Declaring
    # ``__table_args__`` would imply constraints that the ERD does not.


__all__ = ["NotificationTypeRef"]
