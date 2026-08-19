"""``C6 — customer_rep_assignment`` ORM model (customer ↔ representative assignment, with history).

Authority: ``06_ERD.md``, line 41 → ``C6 — customer_rep_assignment``::

    C6 — customer_rep_assignment
    Purpose: Assign which rep serves which customer, with effective date and
             reassignment history.
    PK: id
    FK: customer_id → customer, representative_id → representative
    Important fields: effective_from, effective_to, priority
    Unique: no overlap for (customer_id) per time-window — enforced by
        app/validation
    Classification: C (also acts as history)

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``price_list.py`` (C3), ``warehouse_assignment.py``
(C5), etc.): ``06_ERD.md`` is ``customer_rep_assignment``'s sole authority --
``customer_rep_assignment`` has no detailed section in ``07_DATABASE_SPEC.md``
(confirmed by search: the only ``07_DATABASE_SPEC.md``-adjacent mentions of
"customer_rep_assignment" are the ERD's own text -- no §-numbered
``customer_rep_assignment`` table section of its own).

``customer.py`` (M8) already forward-references this exact table by name in
its own docstring -- *"``customer_rep_assignment`` (C6) is the join, not a
parent/child FK on ..."* -- confirming ``customer_rep_assignment`` is meant to
be a plain join/assignment table hanging off ``customer``'s Aggregate-Root
boundary, not a column folded onto either ``customer`` or ``representative``
themselves.

Both FKs are real from the outset:
    ``customer`` and ``representative`` both already exist in this codebase,
    so ``customer_id`` and ``representative_id`` are declared as real
    ``ForeignKey()`` constraints from the start. Neither is marked nullable in
    the ERD's ``FK:`` line, so both are declared ``NOT NULL`` -- an assignment
    row with no customer or no representative is meaningless, the same
    reasoning ``warehouse_assignment.representative_id`` /
    ``warehouse_assignment.warehouse_id`` already received.

``effective_from`` / ``effective_to`` -- time-bounded validity:
    Same pattern as ``commission_config.effective_from`` / ``effective_to``
    and ``warehouse_assignment.effective_from`` / ``effective_to``:
    ``effective_from`` is ``NOT NULL`` (every assignment has a start of
    validity); ``effective_to`` is nullable, representing open-ended validity
    (an assignment with no scheduled end date -- the customer's *current*
    rep). The ERD gives no explicit nullable annotation for either column
    (same terse style as those two tables' own ERD lines), so this follows
    the identical precedent already established there.

``priority`` -- plain ``Integer``, ``NOT NULL``, no invented default:
    The ERD lists ``priority`` as a bare field name in the same
    ``Important fields:`` line as ``effective_from`` / ``effective_to``, with
    no vocabulary, no bound, and no nullable annotation -- unlike
    ``order_type`` / ``price_type`` (explicit ``PART A`` enums) or
    ``rate`` (an explicit 0..100 bound from ``rate_type()``'s own
    docstring), the ERD gives this column no further shape to transcribe.
    Read together with the table's own Purpose line -- *"with effective date
    and reassignment history"* -- and its ``Classification: C (also acts as
    history)`` tag, ``priority`` is the mechanism that ranks *multiple
    simultaneously-effective* assignment rows for the same customer (e.g. a
    primary rep and one or more backup/fallback reps covering the same
    window), distinct from the effective-date axis that ranks rows
    *sequentially* over time. A plain ``sqlalchemy.Integer`` is used --
    the same type ``product.shelf_life_days`` already uses for an
    unbounded, non-monetary, non-percentage whole number -- rather than one
    of ``database/types.py``'s ``Numeric``/``String`` factories, none of
    which fit an ordinal ranking integer. Declared ``NOT NULL`` with no
    ``server_default``: every assignment row needs a rank to be
    meaningfully ordered against its siblings, and the ERD gives no default
    value to transcribe (unlike ``warehouse_assignment.is_primary``'s
    explicit boolean-default precedent) -- inventing one (e.g. ``1``) would
    be a schema decision with no textual basis, so the caller/application is
    required to supply it explicitly instead.

No ``UniqueConstraint`` / ``ExcludeConstraint`` for the overlap rule --
deliberately, not an oversight:
    This is the one point where ``customer_rep_assignment`` (C6) diverges
    from ``warehouse_assignment`` (C5)'s own precedent, and the divergence is
    dictated by the ERD text itself, not a stylistic choice made here. C5's
    ``Unique:`` line names two *concrete, literal* rules -- an ordinary
    column tuple and a conditional-boolean partial index -- both directly
    expressible as PostgreSQL schema objects, and both were implemented that
    way. C6's ``Unique:`` line instead reads:

        "no overlap for (customer_id) per time-window -- enforced by
        app/validation"

    Two separate signals point the same direction:

    1. *What* the rule is: "no overlap ... per time-window" is a
       **temporal-range non-overlap** constraint -- for a given
       ``customer_id``, no two rows' ``[effective_from, effective_to)``
       intervals may intersect. This is fundamentally different in kind from
       C5's rules: an ordinary ``UNIQUE``/``CHECK`` constraint can only
       compare *fixed column values* against each other or a static
       expression -- it cannot compare one row's *interval* against every
       *other* row's interval to detect a range intersection, because a
       ``CHECK`` is evaluated per-row, with no visibility into sibling rows,
       and a plain ``UNIQUE`` constraint tests only *exact value equality*
       on columns/expressions, not interval containment/overlap. PostgreSQL's
       actual mechanism for this class of rule is ``EXCLUDE USING gist`` with
       a range type and the ``&&`` overlap operator -- a fundamentally
       different constraint kind neither a ``CheckConstraint`` nor a
       ``UniqueConstraint`` reduces to.
    2. *Where* the ERD says the rule lives: "-- enforced by app/validation"
       is an explicit, literal instruction in the ERD's own text, naming the
       enforcement layer directly -- it is not left ambiguous the way, say,
       ``price_list``'s missing ``Unique:`` line was (see
       ``price_list.py``'s own docstring: no rule stated at all there, so
       none was fabricated). Here the ERD *does* state a rule, and *also*
       states, explicitly, which layer enforces it -- application code, not
       the database schema.

    Both readings converge on the same outcome, so no ``CheckConstraint``,
    ``UniqueConstraint``, nor a PostgreSQL ``EXCLUDE`` constraint (via
    ``postgresql.ExcludeConstraint`` + ``btree_gist``/``tstzrange``) is added
    to ``__table_args__`` for the overlap rule, even though ``EXCLUDE`` is
    the textbook mechanism for exactly this "no interval overlap per group
    key" shape in PostgreSQL and was clearly available to reach for. It is
    withheld on direct textual instruction, not because it is technically
    unreachable -- unlike C5, where the ERD's own ``Unique:`` line asked for
    schema-level constraints (composite + conditional-partial) and both were
    built. The overlap rule is therefore a service/validation-layer
    responsibility (e.g. checked in the same transaction that inserts a new
    assignment row, before commit) -- entirely outside this ORM model's
    ``__table_args__``, exactly as instructed.

    No other ``UniqueConstraint`` is fabricated in its place either: the ERD
    gives no other candidate tuple (e.g. guessing
    ``(customer_id, representative_id, effective_from)`` would invent a rule
    with no textual basis, the same discipline ``price_list.py``'s own "No
    ``Unique:`` line -- deliberately NOT fabricated" section already applied
    when the ERD gave nothing to transcribe).

No CHECK constraints:
    The ERD names no vocabulary/enum field on this table (``priority`` is a
    plain unbounded integer, ``effective_from``/``effective_to`` are plain
    timestamps) -- so, unlike ``commission_config``/``price_list``/
    ``discount``/``order_status_history``, there is no vocabulary CHECK to
    write here, the same "no CHECK" outcome ``warehouse_assignment.py``
    reached for the identical reason.

Soft delete -- deliberately absent, same reasoning as
``commission_config``/``warehouse_assignment``/``price_list``:
    The ERD classifies ``customer_rep_assignment`` as ``C (also acts as
    history)`` -- notably *not* "``M`` + soft-deletable" the way
    ``product``/``warehouse``/``app_user`` are. No ``deleted_at`` column is
    declared; the ``effective_from``/``effective_to`` validity window already
    expresses this assignment's own lifecycle (an assignment simply stops
    being current once ``effective_to`` passes or once superseded by a later
    row), the same role that window plays for ``commission_config`` and
    ``warehouse_assignment``.

"Classification: C (also acts as history)" -- UAC, not AAC, on direct
instruction:
    The ERD's own classification is not the plain, bare ``C`` that
    ``commission_config`` (C1) / ``price_list`` (C3) / ``warehouse_assignment``
    (C5) carry -- it appends *"(also acts as history)"*, flagging that this
    table doubles as the append-over-time reassignment log the Purpose line
    describes ("... with effective date and reassignment history"). Despite
    that history-shaped role, this table is **not** treated as an
    append-only/AAC table (contrast ``order_status_history`` /
    ``price_history``, both of which use ``AppendOnlyAuditColumns``): rows
    here are still ordinary *mutable* assignment records (an ``effective_to``
    can legitimately be set/updated on the still-current row once a
    successor assignment begins), and the "history" is produced by
    *accumulating* rows over time via non-overlapping effective windows, not
    by each row being immutable once written. Per direct instruction, this
    model therefore uses ``UniversalAuditColumns`` (UAC) exactly like
    ``commission_config`` / ``price_list`` -- both cited by name as the
    precedent to mirror -- rather than ``AppendOnlyAuditColumns`` (AAC).
    ``CustomerRepAssignment`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), consistent with every other UAC-using model in this
    codebase.

Naming convention:
    Both FKs use ``fk_index_name`` normally --
    ``fk_customer_rep_assignment_customer_id_customer_id`` /
    ``fk_customer_rep_assignment_representative_id_representative_id``.
    There is no ``UniqueConstraint``/``CheckConstraint``/``Index`` on this
    table -- see the dedicated "No ``UniqueConstraint`` / ``ExcludeConstraint``"
    section above -- so ``__table_args__`` is omitted entirely (no empty
    tuple left dangling either).

Column-type choices:

* ``priority`` -- plain ``sqlalchemy.Integer``, ``NOT NULL``, no default.
* ``effective_from`` / ``effective_to`` -- ``DateTime(timezone=True)``.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import fk_index_name


class CustomerRepAssignment(Base, UniversalAuditColumns):
    """``C6 — customer_rep_assignment`` — assign which rep serves which customer, with effective date and reassignment history (Classification: C, also acts as history)."""

    __tablename__ = "customer_rep_assignment"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- customer_id
    customer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("customer_rep_assignment", "customer_id", "customer"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------- representative_id
    representative_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("customer_rep_assignment", "representative_id", "representative"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- effective_from
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ---------------------------------------------------------------- effective_to
    # Nullable -- open-ended validity, same precedent as
    # commission_config.effective_to / warehouse_assignment.effective_to.
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------------ priority
    # Plain unbounded ranking integer -- see module docstring's dedicated
    # section for why no vocabulary/default is invented here.
    priority: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )

    # No __table_args__: the ERD's overlap rule is explicitly
    # "enforced by app/validation", not a database CHECK/UNIQUE/EXCLUDE
    # constraint -- see module docstring's dedicated section.


__all__ = ["CustomerRepAssignment"]
