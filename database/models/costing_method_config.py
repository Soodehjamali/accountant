"""``C2 — costing_method_config`` ORM model (org-level inventory costing method, single-row).

Authority: ``06_ERD.md``, line 37 → ``C2 — costing_method_config``::

    C2 — costing_method_config
    Purpose: Org-level costing method (FIFO/LIFO/WA) — locked after
             financial transactions exist.
    PK: id
    Important fields: method (CostingMethod), locked_at, locked_by → app_user
    Unique: single row enforced
    Classification: C

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``price_list.py`` (C3), ``warehouse_assignment.py``
(C5), ``customer_rep_assignment.py`` (C6), etc.): ``06_ERD.md`` is
``costing_method_config``'s sole authority — ``costing_method_config`` has no
detailed section in ``07_DATABASE_SPEC.md`` (confirmed by search: no
§-numbered ``costing_method_config`` table section exists there).

Enum, ``06_ERD.md`` PART A::

    CostingMethod: FIFO, LIFO, WEIGHTED_AVERAGE

The task prompt shorthands this as "FIFO/LIFO/WA", but ``PART A``'s own
enumerated value set spells the third member out in full as
``WEIGHTED_AVERAGE`` (not ``WA``) — the same source every other enum column
in this codebase (``order_type``, ``price_type``, ``discount_type``, ...) is
read from verbatim, so ``WEIGHTED_AVERAGE`` is the value actually stored, not
the prompt's abbreviation.

``method`` — explicit ERD vocabulary, not an assumption:
    Bounded to ``CostingMethod`` (PART A): ``FIFO`` / ``LIFO`` /
    ``WEIGHTED_AVERAGE``. Like ``commission_config.order_type`` /
    ``price_list.price_type``, this vocabulary is given directly in the ERD
    text. ``state_token_type()`` (``VARCHAR(16)``) fits every member —
    ``WEIGHTED_AVERAGE`` is exactly 16 characters, the longest member and
    still an exact fit — the same "does the longest member fit the factory
    width" check ``price_list.py`` already performs for its own
    ``WHOLESALE`` (9 chars). Declared ``NOT NULL`` with no default: the ERD
    gives no default value to transcribe, and (being the org's *single*
    costing-method row — see ``Unique:`` section below) a bare ``INSERT``
    must supply a real method rather than silently defaulting to one;
    inventing a default (e.g. ``FIFO``) would be a business decision with no
    textual basis, the same discipline ``customer_rep_assignment.priority``
    already applied for an analogous "ERD names the field but gives it no
    default" case.

``locked_at`` — nullable, populated only once locked:
    Per direct instruction: "فقط بعد از قفل شدن پر می‌شه" ("only populated
    once locked"). Before the org's costing method is locked, this column is
    ``NULL``; the Purpose line's own "locked after financial transactions
    exist" describes a one-way transition (unlocked → locked) triggered once
    real financial activity exists, so the column starts ``NULL`` and is
    stamped exactly once, on lock. Same nullable-until-populated shape
    already used for ``discount``/other tables' optional timestamp columns
    in this codebase, just applied to a single-event stamp rather than an
    open-ended validity window.

``locked_by`` — nullable FK, same lifecycle as ``locked_at``:
    The ERD's ``FK:``-folded ``locked_by → app_user`` line carries no
    explicit nullable annotation of its own (the same terse-line style
    ``warehouse_assignment``/``customer_rep_assignment``'s FK lines already
    receive), but it is declared nullable here regardless, on the same
    reasoning the instruction gives ``locked_at`` explicitly: the two
    columns are one atomic "who/when locked this" fact, populated together
    at the same lock event, and meaningless independently of one another —
    there is no state in which a row has a ``locked_at`` timestamp but no
    ``locked_by`` actor (or vice versa). Mirroring ``locked_at``'s explicit
    nullable instruction onto its paired actor column, rather than leaving
    ``locked_by`` ``NOT NULL`` (which would force every row — including the
    still-unlocked initial row — to name a locking user before one exists),
    is the only reading consistent with the Purpose line's "locked after
    financial transactions exist" describing a state that does not yet hold
    for a freshly-created, still-unlocked config row.

``app_user`` already exists in this codebase, so ``locked_by`` is a real
``ForeignKey("app_user.id")`` from the outset, the same treatment every
other FK column pointing at an existing table receives in this codebase.

"Unique: single row enforced" — table-wide singleton, NOT a per-column
conditional-uniqueness case:
    This is deliberately read as a stronger rule than
    ``currency.is_base``'s own ``idx_currency_one_base`` precedent, not an
    identical copy of it, even though both use the same underlying
    mechanism (a ``postgresql_where``-filtered unique partial index). The
    difference:

    * ``currency.is_base`` constrains *which currency rows may claim a
      particular boolean flag* — ``currency`` itself may (and does) have
      many rows; ``is_base`` is a real, independently-meaningful business
      flag that varies per row (``true`` for exactly one currency, ``false``
      for every other currency row, all coexisting in the same table).
    * ``costing_method_config``'s ERD rule is not "at most one row may have
      some flag set" — it is *"single row enforced"*, i.e. the table itself
      may hold **at most one row, full stop**, regardless of any column's
      value. There is no legitimate multi-row state for this table the way
      ``currency`` legitimately holds many non-base rows.

    Per direct instruction, this is implemented with the same
    ``idx_currency_one_base`` idiom — a hand-authored, unique
    ``postgresql_where``-filtered partial index over a boolean column pinned
    to a constant (``true``) — but adapted with **one necessary addition**
    ``currency`` does not need: a ``CHECK`` constraint pinning the helper
    column to that constant value on every row.

    Why the ``CHECK`` is required here but not on ``currency.is_base``:
    ``currency.is_base`` is a real flag that legitimately varies
    (``true``/``false`` both denote real, valid states across different
    currency rows), so its partial index — "unique among rows where
    ``is_base = true``" — already fully expresses the intended rule ("at
    most one base currency"), because rows with ``is_base = false`` are a
    *different, equally valid* business state, not a bypass. Here, by
    contrast, the helper column (``singleton_guard``) carries **no**
    independent business meaning of its own — it exists purely as the
    mechanism the partial index needs a column to pin against. Without a
    ``CHECK`` forcing ``singleton_guard = true`` on every row, nothing would
    stop the application from inserting a *second* (or third, ...) row with
    ``singleton_guard = false``: such a row would fall outside the partial
    index's ``WHERE singleton_guard = true`` predicate entirely, so the
    unique index would never even see it, and the "single row enforced"
    guarantee would silently break for any row inserted with the guard
    column set to anything other than the pinned constant. The ``CHECK``
    closes that loophole by making ``singleton_guard = true`` unconditional
    for *every* row this table will ever hold, which in turn guarantees
    every row — not just some subset — falls inside the partial index's
    filter, so "at most one row may have ``singleton_guard = true``" and
    "at most one row, full stop" become the same statement.

    Concretely, both constraints are declared in ``__table_args__``:

    1. ``CheckConstraint("singleton_guard = true", ...)`` — named via
       ``ck_index_name`` → ``ck_costing_method_config_singleton_guard_true``.
       Forces the helper column to the pinned constant on every row,
       unconditionally.
    2. ``Index(..., "singleton_guard", unique=True,
       postgresql_where=sa_text("singleton_guard = true"))`` — named via
       ``idx_index_name("costing_method_config", "single_row")`` →
       ``idx_costing_method_config_single_row``, the direct structural
       mirror of ``idx_currency_one_base``. Because (1) guarantees every row
       satisfies the ``WHERE`` predicate, this unique partial index reduces
       to an ordinary table-wide uniqueness rule on a column whose value
       never varies — i.e. "at most one row, full stop", enforced entirely
       at the database layer, with no service-layer race condition possible
       (a second concurrent ``INSERT`` will hit the same unique index
       violation Postgres already guarantees under MVCC + a unique index).

    ``singleton_guard`` itself:
        A plain ``Boolean``, ``NOT NULL``, ``DEFAULT true`` — so a bare
        ``INSERT costing_method_config (method, ...)`` with no explicit
        value for this column still satisfies the ``CHECK`` and lands inside
        the partial index's filter without every call site needing to know
        this column exists. Not part of the ERD's own ``Important fields:``
        list — it is a schema-mechanism column, the same kind of documented,
        flagged deviation ``currency.is_base``'s own partial-index comment
        already models ("a documented extension the ERD does not explicitly
        state"), except here the deviation is the column's *existence*
        itself (an ordinary business column would never need a
        CHECK-pinned constant), not just the indexing strategy layered on
        top of an otherwise-ordinary flag.

    An alternative considered and rejected — a bare
    ``UniqueConstraint("singleton_guard")`` (no ``postgresql_where``) would
    achieve the identical "at most one row" outcome given the same CHECK
    (an ordinary unique constraint over a column whose every value is
    pinned to the same constant already forbids a second row). The
    ``postgresql_where``-filtered partial-index form is used instead purely
    because the task explicitly calls for mirroring
    ``idx_currency_one_base``'s own mechanism/idiom by name, not because the
    partial form is functionally stronger here — with the CHECK in place,
    both forms are equivalent.

No other CHECK for the ``method`` vocabulary is omitted:
    A second, ordinary vocabulary ``CheckConstraint`` bounds ``method`` to
    the three ``CostingMethod`` members, named via ``ck_index_name`` →
    ``ck_costing_method_config_method_values`` — the same treatment
    ``commission_config.order_type`` / ``price_list.price_type`` already
    receive for their own explicit ERD enums.

The lock *business rule* itself ("locked after financial transactions
exist") is explicitly out of schema scope:
    Whether it is currently legal to change ``method`` (i.e. whether
    financial transactions already exist elsewhere in the database) is not
    a fact any ``CHECK`` on this table's own columns could express — a
    ``CHECK`` sees only this row's own column values, never the contents of
    ``inventory_transaction``/other tables. That rule is therefore a
    service/validation-layer responsibility (checked before allowing an
    ``UPDATE`` to ``method`` once ``locked_at``/``locked_by`` are already
    populated), the same "app/validation enforces cross-row/cross-table
    rules the schema cannot" split already established for
    ``customer_rep_assignment``'s own no-overlap rule.

Soft delete — deliberately absent, same reasoning as
``commission_config``/``price_list``/``warehouse_assignment``:
    The ERD classifies ``costing_method_config`` as plain ``C``, with no
    "+ soft-deletable" qualifier. No ``deleted_at`` column is declared — a
    singleton config row is not a candidate for soft-deletion in the same
    sense a product/warehouse/user record is; there is exactly one
    (unlocked or locked) row for the life of the org.

Audit-column family — ``UniversalAuditColumns`` (UAC), per instruction:
    Plain ``C`` classification, ordinary mutable config record — the same
    reasoning already established for ``commission_config`` (C1) /
    ``price_list`` (C3) / ``warehouse_assignment`` (C5). ``CostingMethodConfig``
    uses UAC and opts its ``version`` column into SQLAlchemy optimistic
    locking (``__mapper_args__ = {"version_id_col": "version"}``), consistent
    with every other UAC-using model in this codebase.

Naming convention:
    ``locked_by`` uses ``fk_index_name`` normally →
    ``fk_costing_method_config_locked_by_app_user_id``. The ``method``
    vocabulary CHECK uses ``ck_index_name`` → bare descriptor
    ``method_values``, rendering ``ck_costing_method_config_method_values``
    at compile time. The singleton-guard CHECK uses ``ck_index_name`` → bare
    descriptor ``singleton_guard_true``, rendering
    ``ck_costing_method_config_singleton_guard_true``. The partial unique
    index uses ``idx_index_name("costing_method_config", "single_row")`` →
    ``idx_costing_method_config_single_row``.

Column-type choices:

* ``method`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``FIFO`` / ``LIFO`` / ``WEIGHTED_AVERAGE``.
* ``locked_at`` — ``DateTime(timezone=True)``, nullable.
* ``singleton_guard`` — ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT true``,
  CHECK-pinned to ``true`` (schema-mechanism column, not an ERD field).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import state_token_type


class CostingMethodConfig(Base, UniversalAuditColumns):
    """``C2 — costing_method_config`` — org-level costing method (FIFO/LIFO/WEIGHTED_AVERAGE), locked after financial transactions exist (Classification: C)."""

    __tablename__ = "costing_method_config"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- method
    # Explicit ERD vocabulary (PART A CostingMethod): FIFO / LIFO /
    # WEIGHTED_AVERAGE. No default -- see module docstring.
    method: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ---------------------------------------------------------------- locked_at
    # Nullable -- populated only once the org's costing method is locked
    # (per direct instruction; see module docstring).
    locked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ---------------------------------------------------------------- locked_by
    # Nullable -- same lock-event lifecycle as locked_at (see module
    # docstring). Real FK: app_user already exists in this codebase.
    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("costing_method_config", "locked_by", "app_user"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------ singleton_guard
    # Schema-mechanism column, NOT an ERD field -- exists solely so the
    # "single row enforced" rule below has a CHECK-pinned constant column to
    # index against. See module docstring's dedicated "Unique: single row
    # enforced" section for why both the CHECK and the partial unique index
    # are required together.
    singleton_guard: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )

    __table_args__ = (
        CheckConstraint(
            "method IN ('FIFO', 'LIFO', 'WEIGHTED_AVERAGE')",
            name=ck_index_name("costing_method_config", "method_values"),
        ),
        # Pins singleton_guard to the constant every row must carry, so the
        # partial unique index below can never be bypassed by a row with
        # singleton_guard = false -- see module docstring.
        CheckConstraint(
            "singleton_guard = true",
            name=ck_index_name("costing_method_config", "singleton_guard_true"),
        ),
        # "Unique: single row enforced" -- mirrors currency.py's own
        # idx_currency_one_base mechanism (unique partial index over a
        # boolean pinned to true), but here the CHECK above guarantees every
        # row satisfies the WHERE predicate, so this reduces to a table-wide
        # "at most one row" guarantee rather than currency's "at most one
        # row per flag value" guarantee. See module docstring.
        Index(
            idx_index_name("costing_method_config", "single_row"),
            "singleton_guard",
            unique=True,
            postgresql_where=sa_text("singleton_guard = true"),
        ),
    )


__all__ = ["CostingMethodConfig"]
