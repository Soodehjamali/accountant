"""Audit-column mixins for the Enterprise ERP (SIWRMS).

The database spec (``07_DATABASE_SPEC.md``) refers throughout the table
definitions to two reusable audit-column families, each rendering as a
``+UAC`` / ``+AAC`` row in every table's *Column Definitions*:

* **UAC — Universal Audit Columns** — used by ordinary *mutable* transactional
  and master tables (e.g. ``shipment``, ``invoice``, ``payment``). Carries:

      | created_at | TIMESTAMPTZ | NOT NULL | now()    | row creation time, UTC |
      | updated_at | TIMESTAMPTZ | NOT NULL | now()    | last mutate time, UTC  |
      | created_by | UUID        | NOT NULL | —        | posting actor          |
      | updated_by | UUID        | NULL     | NULL     | last mutator (NULL = system) |
      | version    | INTEGER     | NOT NULL | 1        | optimistic-lock token  |

* **AAC — Append-only Audit Columns** — used by immutable / history tables
  (e.g. ``shipment_status_history``, ``audit_log``, ``order_status_history``)
  whose rows are never ``UPDATE``-d or ``DELETE``-d. Carries only:

      | created_at | TIMESTAMPTZ | NOT NULL | now() | row creation time, UTC |
      | created_by | UUID        | NULL     | NULL  | posting actor (NULL = system-generated) |

  — deliberately **no** ``updated_at`` / ``updated_by`` / ``version``, since
  append-only rows have no second mutation to timestamp, no second actor to
  record, and no optimistic-concurrency contention to guard.

Design choices enforced here:

* **Real ``ForeignKey()`` constraints to ``app_user.id``.** ``created_by`` /
  ``updated_by`` (UAC) and ``created_by`` (AAC) are retrofitted to real
  ``ForeignKey("app_user.id")`` columns now that the ``app_user`` model has
  landed — this was previously an explicit, time-bounded deviation
  (plain ``UUID``, no FK) and has now been closed. No explicit constraint
  ``name=`` is passed here: unlike concrete models (which know their own
  ``__tablename__`` and use ``fk_index_name()`` explicitly), a mixin is
  reused across many different tables, so the FK name must vary per
  subclass — this is left to ``NAMING_CONVENTION["fk"]``
  (``database/naming.py``), which substitutes ``%(table_name)s`` at
  constraint-compile time for each concrete table individually, producing
  the exact same ``fk_<table>_<column>_app_user_id`` shape
  ``fk_index_name()`` would have produced by hand. ``app_user`` itself uses
  UAC, so ``AppUser.created_by``/``updated_by`` are self-referencing FKs to
  ``app_user.id`` — valid in SQLAlchemy/PostgreSQL; the very first seeded
  row is a data/bootstrapping concern (e.g. a system-seed script inserting
  itself, or a deferred-constraint seed step), not a schema-validity one.
* **Mixin-free ``created_at`` semantics:** both families use ``now()`` as the
  server-side ``DEFAULT`` (matches every spec row's ``DEFAULT now()``). UAC's
  ``updated_at`` additionally sets ``onupdate=func.now()`` so the column is
  refreshed server-side on every ``UPDATE`` without application code.
* **UTC everywhere:** columns are timezone-aware ``TIMESTAMPTZ``
  (``DateTime(timezone=True)``); the spec's persistent-UTC policy is encoded by
  reading/writing UTC ``datetime`` values, enforced at the service layer — the
  column type itself accepts any tz-aware value and stores it as UTC.
* **Optimistic locking:** the UAC ``version`` column uses SQLAlchemy's native
  ``version_id_col``-compatible pattern — a plain ``Integer`` column seeded
  from ``OPTIMISTIC_LOCK_VERSION_START`` (``database.constants``) and bumped on
  update by the ORM's ``version_id_col`` mechanism on the concrete model
  (the model, not the mixin, wires ``__mapper_args__ = {"version_id_col":
  "version"}``; the mixin only supplies the column).

Scope of this file:

* ``UniversalAuditColumns`` (UAC mixin).
* ``AppendOnlyAuditColumns`` (AAC mixin).

OUT OF SCOPE (and therefore NOT present): concrete models, ``__tablename__`` /
``__mapper_args__``, custom ``TypeDecorator`` types, ``ForeignKey()`` /
``ForeignKeyConstraint``, foreign-key naming helpers, sessions, Alembic.

Authority:
    - 07_DATABASE_SPEC.md  (``+UAC`` / ``+AAC`` rows; ``created_at`` /
      ``event_at`` / ``occurred_at`` columns all ``TIMESTAMPTZ NOT NULL DEFAULT
      now()``; ``version`` ``INTEGER NOT NULL DEFAULT 1`` per ERD ~0.2).
    - database/base.py     (``Base``, ``GuidPk``, ``id_column`` — re-exported
      here only for typed import convenience; not modified by this module).
    - database/constants.py (``OPTIMISTIC_LOCK_VERSION_START`` = 1).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Final

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base
from database.constants import OPTIMISTIC_LOCK_VERSION_START

# ---------------------------------------------------------------------------
# Shared column definitions
# ---------------------------------------------------------------------------
# Reusable typed ``Mapped`` annotations for the columns both families share.
# Kept module-private (underscore-prefixed) since they are building blocks for
# the two mixin classes, not part of the public contract of this module — the
# mixins themselves are the public surface.

_UTC_AWARE_TIMESTAMP: Mapped[datetime.datetime] = mapped_column(
    DateTime(timezone=True),
    nullable=False,
    server_default=func.now(),
)

#: ``created_by`` / ``updated_by`` column shape — plain ``UUID`` (NO FK yet).
#:
#: Reused by both mixins since the only difference between the families is
#: nullability (AAC permits NULL to flag system-generated rows; UAC keeps
#: ``created_by`` NOT NULL as every mutable row has a known creator, while
#: ``updated_by`` is NULL-able because an UPDATE may be performed by a system
#: job — the per-column nullability is set on each mixin's own declaration).
_SYSTEM_NULLABLE_ACTOR_UUID: Mapped[uuid.UUID] = mapped_column(_SAUuid(as_uuid=True))


# ---------------------------------------------------------------------------
# UAC — Universal Audit Columns (mutable tables)
# ---------------------------------------------------------------------------
class UniversalAuditColumns:
    """UAC mixin for ordinary mutable tables.

    Adds the spec's universal audit fields to any concrete model:

        | created_at | TIMESTAMPTZ | NOT NULL | now() | row creation time, UTC |
        | updated_at | TIMESTAMPTZ | NOT NULL | now() | last mutate time, UTC  |
        | created_by | UUID        | NOT NULL | —     | posting actor          |
        | updated_by | UUID        | NULL     | NULL  | last mutator (NULL = system) |
        | version    | INTEGER     | NOT NULL | 1     | optimistic-lock token  |

    Subclass alongside ``Base``::

        class Shipment(Base, UniversalAuditColumns):
            __tablename__ = "shipment"
            ...

    Notes:
        * ``created_at`` / ``updated_at`` are timezone-aware UTC ``TIMESTAMPTZ``
          (``DateTime(timezone=True)``), both ``NOT NULL DEFAULT now()``.
        * ``updated_at`` carries ``onupdate=func.now()`` so SQLAlchemy refreshes
          it server-side on every ``UPDATE`` without application code.
        * ``created_by`` is ``NOT NULL`` (a mutable row always has a known
          creator); ``updated_by`` is ``NULL``-able (a later UPDATE may be done
          by a system job — ``NULL`` indicates the absence of a user actor).
        * Both ``created_by`` and ``updated_by`` are real ``ForeignKey()``
          columns to ``app_user.id`` (retrofitted now that ``app_user``
          exists). No explicit constraint name is passed here — see the
          module docstring's "Real ForeignKey() constraints" section for
          why a mixin can't call ``fk_index_name()`` itself.
        * ``version`` is seeded from ``OPTIMISTIC_LOCK_VERSION_START`` (``1``).
          The concrete model opts into optimistic locking by setting
          ``__mapper_args__ = {"version_id_col": "version"}``; this mixin only
          supplies the column and ``NOT NULL DEFAULT 1``.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    #: Last-mutation timestamp; refreshed server-side on every ``UPDATE``.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    #: Real FK to app_user.id. No explicit name= -- NAMING_CONVENTION["fk"]
    #: substitutes %(table_name)s per concrete subclass. See module
    #: docstring's "Real ForeignKey() constraints" section.
    created_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=False,
    )
    #: Last mutator (NULL = system-generated UPDATE). Real FK to app_user.id,
    #: same no-explicit-name treatment as created_by above.
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )
    #: Optimistic-concurrency row-version token; concrete model wires
    #: ``__mapper_args__ = {"version_id_col": "version"}`` to activate it.
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=OPTIMISTIC_LOCK_VERSION_START,
        server_default=str(OPTIMISTIC_LOCK_VERSION_START),
    )


# ---------------------------------------------------------------------------
# AAC — Append-only Audit Columns (immutable / history tables)
# ---------------------------------------------------------------------------
class AppendOnlyAuditColumns:
    """AAC mixin for immutable / append-only history tables.

    Adds the spec's *append-only* audit fields — **only** creation timestamp
    and creator — to any concrete model:

        | created_at | TIMESTAMPTZ | NOT NULL | now() | row creation time, UTC |
        | created_by | UUID        | NULL     | NULL  | posting actor (NULL = system) |

    Subclass alongside ``Base``::

        class ShipmentStatusHistory(Base, AppendOnlyAuditColumns):
            __tablename__ = "shipment_status_history"
            ...

    Notes:
        * Deliberately omits ``updated_at`` / ``updated_by`` / ``version``:
          these rows are never ``UPDATE``-d (a permissions layer revokes
          ``UPDATE``/``DELETE`` — see the spec's *Soft Delete Strategy* and
          *Business Constraints* on each H-classified table), so there is no
          second mutation to timestamp, no second actor, and no optimistic lock
          to guard against.
        * ``created_by`` is ``NULL``-able because append-only rows are often
          written by automated jobs / triggers (the spec's
          ``shipment_status_history.actor_user_id`` note: *"nullable for
          automated tracking pings"*); "" carries the same ``NULL``-for-system
          convention.
        * ``created_at`` is timezone-aware UTC ``TIMESTAMPTZ NOT NULL DEFAULT
          now()`` (mirrors the spec's ``event_at`` / ``occurred_at`` columns).
        * ``created_by`` is a real ``ForeignKey()`` to ``app_user.id``
          (retrofitted now that ``app_user`` exists), same no-explicit-name
          treatment as ``UniversalAuditColumns.created_by``.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    #: Posting actor (NULL = system-generated). Real FK to app_user.id, same
    #: no-explicit-name treatment as UniversalAuditColumns.created_by above.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )


__all__: Final[list[str]] = [
    "AppendOnlyAuditColumns",
    "UniversalAuditColumns",
]
