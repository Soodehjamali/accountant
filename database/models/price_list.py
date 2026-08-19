"""``C3 — price_list`` ORM model (named price list binding price versions to a scope).

Authority: ``06_ERD.md``, line 38 → ``C3 — price_list``::

    C3 — price_list
    Purpose: Named price list binding price versions to a scope (customer
             tier, rep, region, export).
    PK: id
    Important fields: name, price_type (PriceType), currency_id →
                      currency, owner_scope (description), is_active
    Classification: C

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``discount.py`` (H3), etc.): ``06_ERD.md``
is ``price_list``'s sole authority — ``price_list`` has no detailed section
in ``07_DATABASE_SPEC.md`` (confirmed by search: the only
``07_DATABASE_SPEC.md`` mentions of "price_list" are ``price_history``'s own
``price_list_id`` FK note (H1, not yet built) — no §-numbered ``price_list``
table section of its own).

Enum, ``06_ERD.md`` PART A::

    PriceType: RETAIL, REP, WHOLESALE, EXPORT, PROMO

No separate ``FK:`` line — a format quirk of this ERD entry, not a missing
FK:
    Every other table implemented so far in this codebase gives FKs their
    own dedicated ``FK:`` line (e.g. ``commission_config``: *"FK:
    representative_id → representative..."*). ``price_list``'s ERD entry has
    no such line at all — ``currency_id → currency`` appears only inline,
    folded into the ``Important fields:`` list. This is read as a formatting
    variance in the ERD's own entry, not a signal that ``currency_id`` is
    somehow not a real foreign key: the ``→ currency`` arrow notation is the
    same FK notation used everywhere else in this ERD, and ``currency`` (R5)
    already exists in this codebase, so ``currency_id`` is declared as an
    ordinary real ``ForeignKey("currency.id")`` here, identical in kind to
    every other FK column in this codebase — the absence of a separate
    ``FK:`` line changes nothing about the column's own shape.

``currency_id`` nullability -- treated as ``NOT NULL``:
    Unlike ``discount.py``'s four scope FKs (each explicitly annotated
    ``(nullable)`` in that table's own ERD ``FK:`` line) or
    ``commission_config.representative_id`` (explicitly annotated
    nullable), this ERD entry gives ``currency_id`` no nullability
    annotation at all. Every price on a price list is denominated in some
    currency — there is no "no currency" case analogous to
    ``commission_config.representative_id``'s "global default" reading —
    so, absent an explicit nullable marker (the consistent signal this ERD
    uses elsewhere whenever a column *is* meant to be optional),
    ``currency_id`` is declared ``NOT NULL``.

``name`` -- type-width choice:
    ``name_type()`` (``VARCHAR(160)``) — the same factory used for
    ``product.name`` / ``warehouse.name`` / other human display names in
    this codebase (per that factory's own docstring: *"human display
    names"*). ``price_list.name`` is squarely in that family (a
    human-readable label for the price list itself, e.g. "Q3 2026 Export
    Wholesale"). Declared ``NOT NULL`` — every price list needs a name to
    be usable/selectable by the application, and the ERD gives no nullable
    annotation for it.

``price_type`` -- explicit ERD vocabulary, not an assumption:
    Bounded to ``PriceType`` (PART A): ``RETAIL`` / ``REP`` / ``WHOLESALE``
    / ``EXPORT`` / ``PROMO``. Like ``commission_config.order_type`` /
    ``discount.discount_type``, this vocabulary is given directly in the ERD
    text, not assumed. ``state_token_type()`` (``VARCHAR(16)``) fits every
    member (``WHOLESALE`` is the longest at 9 characters) — the same
    treatment those two columns already receive, with a matching CHECK
    named via ``ck_index_name``.

``owner_scope`` -- type choice, following the ERD's own parenthetical:
    The ERD's own text for this field is ``"owner_scope (description)"`` --
    the parenthetical itself names the intended shape: a free-text
    description of the scope this list is bound to (e.g. "customer tier",
    "rep", "region", "export" per the table's own Purpose line), not a
    bounded vocabulary token. ``description_type()`` (``VARCHAR(255)``) is
    therefore used directly — the same factory ``invoice_line.description``
    already uses, and the ERD's own wording makes this the direct match
    rather than a "closest existing factory" placeholder judgment call
    (contrast ``order.fulfillment_mode``'s ``VARCHAR(24)``-for-
    ``VARCHAR(20)`` placeholder case, where no such explicit steer was
    given). Declared ``NOT NULL`` — no nullable annotation given.

``is_active`` -- boolean flag, not an enum/state machine:
    A plain ``Boolean``, ``NOT NULL``, defaulting to ``True`` (a
    newly-created price list is active by default; deactivation is an
    explicit later action). This is a simpler on/off flag than
    ``order.state`` / ``stock_reservation.state``'s multi-value vocabulary
    CHECKs — the ERD gives ``is_active`` no enumerated value set, just a
    single field name suggesting boolean semantics, consistent with
    ``product.is_active`` / similar boolean flags already used elsewhere in
    this codebase's M/C-classified tables.

No ``Unique:`` line -- deliberately NOT fabricated:
    Unlike every other table implemented so far (each of which gives a
    concrete ``Unique:`` line — a literal column, e.g. ``order_number``, or
    an explicit tuple, e.g. ``(representative_id, product_category_id,
    order_type, effective_from)`` — or, in ``discount``'s case, at least an
    explicit-if-vague ``"(scope_combo) per policy"`` pointer), ``price_list``'s
    ERD entry has **no** ``Unique:`` line at all — not even a vague one.
    Fabricating a ``UniqueConstraint`` here (e.g. guessing ``name`` should
    be globally unique, or ``(price_type, currency_id, owner_scope)``)
    would invent a schema rule with zero textual basis, a stronger
    violation of "spec is authority" than ``discount``'s case (which at
    least named the concept, just not the columns). None is added.

No CHECK beyond ``price_type`` vocabulary:
    The ERD gives no other bounded field on this table (``name`` /
    ``owner_scope`` are free text, ``is_active`` is a plain boolean,
    ``currency_id`` is a plain FK) — so ``ck_price_list_price_type_values``
    is the only CHECK constraint on this table.

Soft delete -- deliberately absent, same reasoning as ``commission_config``/
``discount``:
    The ERD classifies ``price_list`` as plain ``C`` with no
    "+ soft-deletable" qualifier. No ``deleted_at`` column is declared;
    ``is_active`` already gives this table its own on/off lifecycle flag
    (a deactivated price list is simply ``is_active = false``, not a
    soft-deleted row), the same role ``valid_to`` plays for
    ``commission_config``/``discount``'s own lifecycle, just modeled as a
    boolean here instead of a validity window since the ERD gives this
    table a flag, not a window.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Plain ``C`` classification, ordinary mutable master/config record —
    the same reasoning already established for ``commission_config`` (C1)
    and ``discount`` (H3, itself classified "C/H" but using UAC for the
    same "ordinary mutable record" reason). ``PriceList`` uses UAC and opts
    its ``version`` column into SQLAlchemy optimistic locking
    (``__mapper_args__ = {"version_id_col": "version"}``), consistent with
    every other UAC-using model in this codebase.

Naming convention:
    ``currency_id`` uses ``fk_index_name`` normally ->
    ``fk_price_list_currency_id_currency_id``. The ``price_type`` vocabulary
    CHECK uses ``ck_index_name`` normally -> ``ck_price_list_price_type_
    values``. There is no ``UniqueConstraint`` — see the section above.

Column-type choices:

* ``name`` -- ``name_type()`` -> ``VARCHAR(160)``.
* ``price_type`` -- ``state_token_type()`` -> ``VARCHAR(16)``.
* ``owner_scope`` -- ``description_type()`` -> ``VARCHAR(255)``.
* ``is_active`` -- ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT true``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import description_type, name_type, state_token_type


class PriceList(Base, UniversalAuditColumns):
    """``C3 — price_list`` — named price list binding price versions to a scope (customer tier, rep, region, export) (Classification: C)."""

    __tablename__ = "price_list"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ---------------------------------------------------------------- name
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # ---------------------------------------------------------- price_type
    # Explicit ERD vocabulary (PART A PriceType): RETAIL/REP/WHOLESALE/
    # EXPORT/PROMO.
    price_type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------ currency_id
    # NOT NULL -- see module docstring's dedicated nullability note. No
    # separate "FK:" line in the ERD entry, but a real FK regardless -- see
    # module docstring's "No separate FK: line" section.
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("price_list", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ owner_scope
    # Free-text scope description per the ERD's own "(description)"
    # parenthetical -- see module docstring's dedicated note.
    owner_scope: Mapped[str] = mapped_column(
        description_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- is_active
    is_active: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )

    __table_args__ = (
        # CHECK: price_type vocabulary. No other CHECK on this table --
        # see module docstring's "No CHECK beyond price_type vocabulary".
        CheckConstraint(
            "price_type IN ('RETAIL', 'REP', 'WHOLESALE', 'EXPORT', 'PROMO')",
            name=ck_index_name("price_list", "price_type_values"),
        ),
    )


__all__ = ["PriceList"]
