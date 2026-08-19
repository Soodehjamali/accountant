"""``M11 — user_role`` ORM model (RBAC user-role junction).

Authority: ``06_ERD.md``, line 33 → ``user_role``::

    M11 — user_role (J — N:N between app_user and role)
    Purpose: Assign roles to users.
    PK: composite (user_id, role_id)
    FK: user_id → app_user, role_id → role
    Important fields: assigned_at, assigned_by
    Unique: (user_id, role_id)
    Classification: J

Deliberate mirror of ``role_permission.py`` (R8), per explicit instruction:
    ``06_ERD.md`` line 142 groups ``role_permission`` and ``user_role``
    together explicitly as the two junctions *"already existed"* /
    consistently-named alongside each other (both classified ``J`` despite
    being numbered within R/M rather than a pure ``J1``/``J2`` slot -- see
    ``06_ERD.md``'s own G.5 count line: *"2 more junctions classified J but
    numbered within R/M: role_permission, user_role"*). This model
    therefore deliberately mirrors ``role_permission.py``'s own structural
    choices (composite PK instead of a surrogate ``id``, AAC instead of
    UAC, the redundant-but-explicit ``UniqueConstraint`` treatment) rather
    than re-deriving them independently -- the same "sibling table, mirror
    the existing precedent" instruction already followed for
    ``customer_contact.py`` mirroring ``representative_contact.py``. Every
    reasoning note on ``role_permission.py`` about the composite-PK /ACC/
    redundant-UNIQUE choices applies identically here; this docstring
    restates them for ``user_role``'s own columns/names and adds the two
    columns ``role_permission`` does not have (``assigned_at`` /
    ``assigned_by``).

Composite PK, no surrogate ``id`` -- same as ``role_permission.py``:
    The ERD requires this junction's two foreign keys to be its composite
    PK (``PK: composite (user_id, role_id)``), so ``user_id`` and
    ``role_id`` are each declared ``primary_key=True`` and no ``GuidPk``
    ``id`` column is added -- identical treatment to
    ``role_permission.role_id``/``permission_id``.

Both FKs are real from the outset:
    ``app_user`` and ``role`` both already exist in this codebase, so
    ``user_id`` and ``role_id`` are declared as real ``ForeignKey()``
    constraints from the start.

``assigned_by`` -- treated as a real FK to ``app_user.id`` despite no ``→``
arrow in the ERD's own text:
    The ERD's ``Important fields:`` line lists ``assigned_by`` as a bare
    name, the same terse style ``price_history.reason`` had (no arrow) --
    but unlike ``reason`` (which stayed free text because nothing about
    "why a price changed" implies a specific reference target),
    ``assigned_by`` is unambiguously "which ``app_user`` granted this
    role" by its own name and by direct instruction for this task, the
    same reasoning already used for ``stock_reservation.reserved_by`` /
    ``order_status_history.actor_user_id`` (both real FKs to
    ``app_user.id`` despite comparably terse ERD phrasing, because the
    business meaning leaves no other sensible target). ``assigned_by`` is
    declared **nullable** -- system/bootstrap role grants (e.g. an initial
    admin role assigned during account provisioning, with no specific
    human granting actor) have no natural actor to record, mirroring AAC's
    own ``created_by`` nullable-for-system convention on this same
    AAC-using table.

``assigned_at`` -- a business column distinct from AAC's own
``created_at``, despite near-identical semantics:
    Both columns answer "when did this happen", which invites the
    question of redundancy -- but the ERD lists ``assigned_at`` as its own
    ``Important fields:`` entry, separate from (and in addition to) the
    ``+AAC`` row every AAC-using table already carries, the same
    "business column alongside the mixin's own audit column" pattern
    already established for ``stock_reservation.reserved_by`` (next to
    UAC's own ``created_by``) and ``order_status_history.actor_user_id``
    (next to AAC's own ``created_by``). Declared ``NOT NULL DEFAULT now()`` (``DateTime(timezone=True)``,
    ``server_default=func.now()``) -- the same ``now()``-defaulted
    treatment given to every other spec'd business timestamp in this
    codebase (e.g. ``order_status_history.event_at``). Kept as a distinct
    column rather than collapsed into ``created_at`` because the ERD
    explicitly names it separately and a future data-migration/backfill of
    historical role grants could plausibly set ``assigned_at`` to a real
    historical grant date while ``created_at`` reflects the row's own
    (later) insertion time into this table -- the same kind of
    provenance-vs-insertion distinction ``price_history.effective_from``
    draws relative to its own ``created_at``.

Unique constraint -- same redundant-but-explicit treatment as
``role_permission.py``:
    The ERD explicitly lists both the composite primary key AND a UNIQUE
    constraint on the identical ``(user_id, role_id)`` pair -- the UNIQUE
    constraint is redundant in relational terms because the primary key
    already prohibits duplicates, but it is retained as
    ``uq_user_role_user_id_role_id`` as an explicit, specification-mandated
    constraint rather than silently dropped, the exact same treatment
    ``role_permission.py``'s own docstring documents for its own
    ``(role_id, permission_id)`` pair.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), same as
``role_permission.py``:
    ``created_at`` / ``created_by`` only. Each row records a role grant as
    an insertion-only association -- matching the linked-at /
    created-at-only shape ``role_permission.py``'s own docstring describes
    for J1 ``invoice_order`` and uses for itself. The ERD gives this table
    the same unqualified ``Classification: J`` as ``role_permission``, with
    no additional soft-delete or mutability qualifier, so no other audit
    family is considered.

Naming convention:
    Each foreign key is explicitly named through ``fk_index_name``
    (``fk_user_role_user_id_app_user_id``, ``fk_user_role_role_id_role_id``,
    plus ``assigned_by``'s own ``fk_user_role_assigned_by_app_user_id``).
    The specification-mandated pair UNIQUE is explicitly named through
    ``uq_index_name`` with ``composite_descriptor`` ->
    ``uq_user_role_user_id_role_id``.

Column-type choices:

* ``assigned_at`` -- ``DateTime(timezone=True)``, ``NOT NULL DEFAULT
  now()``.
* ``assigned_by`` -- plain ``Uuid`` FK column, nullable.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base
from database.mixins import AppendOnlyAuditColumns
from database.naming import composite_descriptor, fk_index_name, uq_index_name


class UserRole(Base, AppendOnlyAuditColumns):
    """``M11 — user_role`` — RBAC user-role junction (Classification: J)."""

    __tablename__ = "user_role"

    # Composite identity: a user may receive each role at most once.
    user_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("user_role", "user_id", "app_user"),
        ),
        primary_key=True,
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "role.id",
            name=fk_index_name("user_role", "role_id", "role"),
        ),
        primary_key=True,
        nullable=False,
    )

    # --------------------------------------------------------------- assigned_at
    # Business column, distinct from AAC's own created_at -- see module
    # docstring's dedicated section.
    assigned_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # --------------------------------------------------------------- assigned_by
    # Real FK to app_user.id despite no "->" arrow in the ERD's own text --
    # see module docstring's dedicated section. Nullable = system/bootstrap
    # grant, mirroring AAC's own created_by nullable-for-system convention.
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("user_role", "assigned_by", "app_user"),
        ),
        nullable=True,
    )

    __table_args__ = (
        # Redundant with the composite PK but explicitly required by the
        # ERD. See module docstring's dedicated section.
        UniqueConstraint(
            "user_id",
            "role_id",
            name=uq_index_name(
                "user_role",
                composite_descriptor(("user_id", "role_id")),
            ),
        ),
    )


__all__ = ["UserRole"]
