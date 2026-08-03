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

* **Real ``ForeignKey("app_user.id")`` on ``created_by`` / ``updated_by``.**
  ``app_user`` (M10) now exists in this codebase, so the previously-deferred
  FK deviation is retrofitted here: both mixins' actor columns carry a real
  ``ForeignKey("app_user.id")`` rather than a plain ``UUID``. This is the
  codebase-wide follow-up flagged in ``order.py``'s own docstring ("known,
  PRE-EXISTING gap") and in every other deferred-FK model's docstring
  (``warehouse.responsible_user_id``, ``inventory_transaction.actor_user_id``
  / ``lot_id``) as depending on ``app_user`` landing — it has landed, so the
  deviation is resolved here rather than left open.
* **``@declared_attr`` + an explicit ``name=fk_index_name(...)`` — NOT a
  bare column-level ``mapped_column()`` with an unnamed ``ForeignKey()``.**
  This is a correction of a real bug found while implementing this change,
  recorded here so it isn't reintroduced:

  An unnamed ``ForeignKey("app_user.id")`` declared as a plain mixin-level
  ``mapped_column()`` (relying on ``NAMING_CONVENTION["fk"]`` to supply the
  name automatically) looks correct and *does* produce the right name when
  every model happens to be imported after ``app_user`` — but
  ``NAMING_CONVENTION["fk"]`` includes ``%(referred_table_name)s`` /
  ``%(referred_column_0_name)s`` tokens, and resolving those tokens requires
  SQLAlchemy to eagerly look up the *target* ``Table`` object
  (``fk.column`` / ``NoReferencedTableError`` is raised from exactly this
  path) at the moment the FK-bearing table itself is constructed — i.e. at
  class-body-execution time for every UAC/AAC-using model, not lazily at
  first use. Since this project's ``database/models/__init__.py``
  deliberately does **no** eager model loading (models are imported
  individually), any import of e.g. ``database.models.order`` (or any other
  UAC/AAC consumer) *without* ``database.models.app_user`` already imported
  first now raises ``NoReferencedTableError`` — a hard crash, not a silent
  degradation, and one that is entirely import-order dependent (works from
  ``check_mappers.py``'s particular import sequence, breaks from a bare
  ``from database.models.order import Order``). This is functionally the
  same class of import-order fragility ``database/base.py``'s own docstring
  already warns about for FK column *typing* (its "type_annotation_map"
  section) — here it manifests for constraint *naming* instead, and as a
  hard exception rather than a silent ``NullType()``.

  The fix: each mixin FK column is declared via ``@declared_attr`` instead
  of a bare class-level ``mapped_column()``. ``declared_attr`` methods run
  once per concrete subclass, with that subclass's ``__tablename__`` already
  available — so an **explicit** ``name=fk_index_name(cls.__tablename__,
  "created_by"/"updated_by", "app_user")`` can be supplied per-subclass, the
  same explicit-naming style every concrete model's own FK columns already
  use. Passing an explicit ``name=`` bypasses the naming-convention template
  substitution entirely (no ``%(referred_table_name)s`` token to resolve),
  so no eager cross-table lookup happens at class-definition time and the
  import-order dependency is gone. The produced names are unchanged from
  what the naming convention would have produced anyway (e.g.
  ``fk_order_created_by_app_user_id``,
  ``fk_warehouse_updated_by_app_user_id``) — this is a robustness fix, not a
  naming-scheme change.
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
``__mapper_args__``, custom ``TypeDecorator`` types, ``ForeignKeyConstraint``,
sessions, Alembic.

Authority:
    - 07_DATABASE_SPEC.md  (``+UAC`` / ``+AAC`` rows; ``created_at`` /
      ``event_at`` / ``occurred_at`` columns all ``TIMESTAMPTZ NOT NULL DEFAULT
      now()``; ``version`` ``INTEGER NOT NULL DEFAULT 1`` per ERD ~0.2).
    - database/base.py     (``Base``, ``GuidPk``, ``id_column`` — re-exported
      here only for typed import convenience; not modified by this module).
    - database/constants.py (``OPTIMISTIC_LOCK_VERSION_START`` = 1).
    - database/naming.py   (``fk_index_name`` — used explicitly per
      ``declared_attr``, see "Design choices" above).
    - database/models/app_user.py (``M10 — app_user``, ``__tablename__ =
      "app_user"`` — the FK target now used by both mixins).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Final

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql import func

from database.base import Base
from database.constants import OPTIMISTIC_LOCK_VERSION_START
from database.naming import fk_index_name


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
        * Both ``created_by`` and ``updated_by`` carry a real
          ``ForeignKey("app_user.id")``, declared via ``@declared_attr`` with
          an explicit ``name=fk_index_name(...)`` — see the module
          docstring's "Design choices" section for why (import-order
          fragility fix, not a stylistic choice).
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

    @declared_attr
    def created_by(cls) -> Mapped[uuid.UUID]:  # noqa: N805 -- SQLAlchemy declared_attr convention
        return mapped_column(
            _SAUuid(as_uuid=True),
            ForeignKey(
                "app_user.id",
                name=fk_index_name(cls.__tablename__, "created_by", "app_user"),
            ),
            nullable=False,
        )

    @declared_attr
    def updated_by(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        """Last mutator (NULL = system-generated UPDATE)."""

        return mapped_column(
            _SAUuid(as_uuid=True),
            ForeignKey(
                "app_user.id",
                name=fk_index_name(cls.__tablename__, "updated_by", "app_user"),
            ),
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
        * ``created_by`` carries a real ``ForeignKey("app_user.id")``,
          declared via ``@declared_attr`` with an explicit
          ``name=fk_index_name(...)`` — see UAC's own docstring / the module
          docstring's "Design choices" section for why.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    @declared_attr
    def created_by(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        """Posting actor (NULL = system-generated)."""

        return mapped_column(
            _SAUuid(as_uuid=True),
            ForeignKey(
                "app_user.id",
                name=fk_index_name(cls.__tablename__, "created_by", "app_user"),
            ),
            nullable=True,
        )


__all__: Final[list[str]] = [
    "AppendOnlyAuditColumns",
    "UniversalAuditColumns",
]
