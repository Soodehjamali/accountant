"""``C4 — system_config`` ORM model (key/value runtime tunables).

Authority: ``06_ERD.md``, line 39 → ``C4 — system_config``::

    C4 — system_config
    Purpose: Key/value runtime tunables (negative-stock-allowed flag,
             reservation-auto-expiry-minutes, adjustment threshold, etc.).
    PK: id / key unique
    Important fields: key, value, category
    Classification: C

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``costing_method_config.py`` (C2),
``price_list.py`` (C3), etc.): ``06_ERD.md`` is ``system_config``'s sole
authority — ``system_config`` has no detailed section in
``07_DATABASE_SPEC.md`` (confirmed by search: no §-numbered
``system_config`` table section exists there).

FK: none:
    Unlike every other C-class table implemented so far in this codebase
    (``commission_config`` -> representative/product_category,
    ``costing_method_config`` -> app_user, ``warehouse_assignment`` ->
    representative/warehouse, ``customer_rep_assignment`` ->
    customer/representative), the ERD gives ``system_config`` no ``FK:``
    line at all — a flat, standalone key/value table with no relationship to
    any other entity. No ``ForeignKey()`` appears anywhere on this model.

"PK: id / key unique" — two independent uniqueness facts, not one:
    Read literally as the task instructs: this table has an ordinary
    surrogate ``id`` primary key (the same ``GuidPk``/``id_column()`` every
    other table in this codebase uses), *and*, separately, ``key`` itself
    must be independently unique — not a composite
    ``UniqueConstraint("id", "key")`` (which would be vacuous: ``id`` is
    already the PK and therefore already unique on its own, so pairing it
    with ``key`` would not actually constrain ``key`` at all — two rows
    could still carry the same ``key`` value under two different ``id``s).
    ``key`` is therefore given its own column-level ``unique=True``, the
    exact same idiom ``carrier.code`` / ``city_ref.code`` /
    ``bot_platform_ref.code`` / ``app_user.username`` /
    ``app_user.email`` already use in this codebase for a
    "surrogate PK + independently-unique business key" shape — the shared
    metadata's naming convention renders this as an ordinary
    ``uq_system_config_key`` unique constraint, not a composite one.

``key`` — the lookup token administrators/services query by:
    ``type_token_type()`` -> ``VARCHAR(40)``, the codebase's
    "polymorphic / enum-style token discriminator" factory (per its own
    docstring). The ERD's own worked examples --
    ``negative-stock-allowed``, ``reservation-auto-expiry-minutes``,
    ``adjustment-threshold`` -- are all machine-readable, kebab/snake-style
    identifier tokens rather than human display names (ruling out
    ``name_type()``) or short codes like a SKU/ISO code (ruling out
    ``code_short_type()``'s narrower "short code" framing, even though both
    factories happen to share the same 40-char width) -- ``type_token_type()``
    is the closest semantic fit already in ``database/types.py`` for "a
    growing vocabulary of discriminator-shaped string tokens", and its
    40-char width comfortably covers the longest worked example
    (``reservation-auto-expiry-minutes``, 32 chars) with headroom for future
    keys. Declared ``NOT NULL`` -- a config row with no key is meaningless --
    and given column-level ``unique=True`` per the ``PK: id / key unique``
    line (see dedicated section above).

``value`` -- deliberately a single free-text column, NOT a typed/discriminated
column, and NOT ``sqlalchemy.Text()``:
    The ERD's own Purpose line gives three worked examples of what this
    table stores -- a *boolean* flag (``negative-stock-allowed``), an
    *integer* (``reservation-auto-expiry-minutes``), and an unnamed
    *numeric* threshold (``adjustment threshold``) -- three different
    logical types sharing one physical column. Per direct instruction, the
    interpretation of *which* logical type a given row's ``value`` holds is
    an application-layer concern, not a schema-layer one: this table has no
    parallel "value_type"/discriminator column (the ERD's own
    ``Important fields:`` list names only ``key, value, category`` -- no
    fourth column), so there is no schema-visible signal to route a
    strongly-typed column choice (e.g. a nullable ``Boolean`` + nullable
    ``Numeric`` + nullable ``String`` trio) against, and inventing a
    discriminator column the ERD does not list would be fabricating schema
    the spec does not ask for -- the same discipline already applied to
    ``price_list``'s deliberately-absent ``Unique:`` line and
    ``customer_rep_assignment``'s deliberately-absent overlap constraint.
    A single free-text column is therefore the only shape consistent with
    both the ERD's flat three-column field list and the "app/validation
    interprets it" reasoning: the application layer reads ``key`` first,
    looks up (in application code, not the database) what logical type
    *that* key is expected to hold, and parses/coerces ``value`` --
    e.g. ``"true"``/``"false"`` for a boolean flag, ``"30"`` for an integer
    minute count -- accordingly. The database's role is limited to storing
    and returning the literal text, exactly the same "app/validation owns
    the semantics the schema cannot express" split already used for
    ``customer_rep_assignment``'s overlap rule and
    ``costing_method_config``'s lock-eligibility rule.

    Column-type choice within "free text": ``token_type()`` ->
    ``VARCHAR(120)`` is used rather than ``sqlalchemy.Text()`` (the
    unbounded type ``order_status_history.note`` already establishes as
    this codebase's precedent for genuinely unbounded free text). The
    distinction: ``order_status_history.note`` is a human-authored,
    open-ended annotation with no natural upper bound (the spec's own
    column type there is literally unbounded ``TEXT``), whereas every
    worked example of a ``system_config.value`` in the ERD's own Purpose
    line -- a boolean token, an integer, a numeric threshold -- is a short,
    machine-parsed scalar serialized as text, structurally much closer to
    the "ephemeral tokens / short serialized values" family
    ``token_type()``'s own docstring already describes (session tokens,
    bank references, mime-type-adjacent values) than to unbounded prose.
    ``VARCHAR(120)`` comfortably holds any realistic boolean/int/numeric/
    short-string tunable value with generous headroom, without silently
    inviting this column to be (mis)used for long free-form text the way an
    unbounded ``Text()`` column would implicitly permit -- a config *value*
    growing into paragraph-length content would itself be a modeling smell
    for this table. Declared ``NOT NULL`` -- the ERD gives no nullable
    annotation, and a config row with a key but no value is not a
    meaningful "known tunable" the way, say, ``commission_config``'s
    optional ``representative_id`` (an explicit "global default" case) is.

``category`` -- short grouping token, not a bounded enum:
    The ERD's own worked category names (in the task prompt, echoing the
    ERD's own domain) -- ``"inventory"``, ``"reservation"`` -- are short,
    lower-case grouping labels, not a fixed, exhaustively-enumerated
    vocabulary the way ``order_type``/``price_type``/``method`` are (no
    ``PART A`` enum entry exists for a "system config category" type) --
    so, unlike those columns, no ``CheckConstraint`` bounds ``category``'s
    values; new categories can be introduced by application code writing a
    new string without a schema migration, the same "runtime-editable, not
    a fixed enum" reasoning already applied to
    ``price_list.owner_scope``/``price_list.price_type``'s *sibling*
    free-text ``owner_scope`` column (as opposed to its bounded
    ``price_type`` column). ``state_token_type()`` -> ``VARCHAR(16)`` is
    used: both worked examples (``inventory`` = 9 chars, ``reservation`` =
    11 chars) fit comfortably within this codebase's existing "short state /
    channel / type token" width, the same factory
    ``commission_config.order_type`` / ``price_list.price_type`` /
    ``costing_method_config.method`` already use for comparably short
    classification tokens -- chosen over the wider ``type_token_type()``
    (already used for ``key`` above) because a *grouping label* is
    semantically closer to this codebase's other short classification
    tokens than to a polymorphic discriminator. Declared ``NOT NULL`` -- the
    ERD gives no nullable annotation, and an ungrouped tunable would defeat
    the column's own "grouping keys" purpose.

No CHECK constraints:
    Neither ``value`` (free text, semantics owned by the application layer
    per the dedicated section above) nor ``category`` (an open,
    runtime-extensible label set, not a ``PART A`` enum) is a bounded
    vocabulary column here, so — unlike ``commission_config``/``price_list``/
    ``costing_method_config`` — this table has no vocabulary CHECK to write.

Soft delete -- deliberately absent, same reasoning as
``commission_config``/``price_list``/``costing_method_config``:
    The ERD classifies ``system_config`` as plain ``C``, with no
    "+ soft-deletable" qualifier. No ``deleted_at`` column is declared — a
    tunable that is no longer needed is simply removed (``DELETE``) or
    repurposed, not soft-deleted, and the ERD gives this table no lifecycle
    flag/window of its own the way ``price_list.is_active`` or
    ``commission_config.effective_to`` do.

Audit-column family -- ``UniversalAuditColumns`` (UAC), per instruction:
    Plain ``C`` classification, ordinary mutable config record -- the same
    reasoning already established for ``commission_config`` (C1) /
    ``costing_method_config`` (C2) / ``price_list`` (C3).
    ``SystemConfig`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), consistent with every other UAC-using model in this
    codebase.

Naming convention:
    ``key`` uses column-level ``unique=True``, which the shared metadata
    naming convention renders as ``uq_system_config_key`` (see dedicated
    "PK: id / key unique" section above -- this is NOT a composite
    constraint). No FK, CHECK, or hand-authored ``Index`` exists on this
    table, so ``__table_args__`` is omitted entirely (no empty tuple left
    dangling), the same omission ``price_list.py`` makes for its own
    absent ``Unique:`` line.

Column-type choices:

* ``key`` -- ``type_token_type()`` -> ``VARCHAR(40)``, column-level
  ``unique=True``.
* ``value`` -- ``token_type()`` -> ``VARCHAR(120)``, free text; type
  interpretation (boolean/int/string) is an application-layer concern (see
  dedicated note above).
* ``category`` -- ``state_token_type()`` -> ``VARCHAR(16)``, open grouping
  label, not a bounded enum.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import state_token_type, token_type, type_token_type


class SystemConfig(Base, UniversalAuditColumns):
    """``C4 — system_config`` — key/value runtime tunables (Classification: C)."""

    __tablename__ = "system_config"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- key
    # Column-level unique=True -- independently unique, NOT part of a
    # composite constraint with `id`. See module docstring's dedicated
    # "PK: id / key unique" section.
    key: Mapped[str] = mapped_column(
        type_token_type(),
        nullable=False,
        unique=True,
    )

    # --------------------------------------------------------------- value
    # Free text -- type interpretation (boolean/number/string) is an
    # application-layer concern, not a schema-layer one. See module
    # docstring's dedicated "value" section for why this is a single free
    # column rather than a typed/discriminated trio.
    value: Mapped[str] = mapped_column(
        token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------ category
    # Open grouping label (e.g. "inventory", "reservation") -- not a bounded
    # PART A enum, so no CHECK constraint. See module docstring.
    category: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )


__all__ = ["SystemConfig"]
