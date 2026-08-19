"""``H1 — price_history`` ORM model (immutable versioned selling price).

Authority: ``06_ERD.md``, line 44 → ``H1 — price_history``::

    H1 — price_history
    Purpose: Immutable versioned selling price per (product, price_type,
             scope) (BRF §2, §3).
    PK: id
    FK: product_id → product, price_list_id → price_list,
        currency_id → currency
    Important fields: price_type (PriceType), unit_price (numeric),
        effective_from, effective_to (nullable), is_promo (bool),
        promo_valid_from, promo_valid_to, reason
    Unique: (product_id, price_type, price_list_id, effective_from);
        no overlapping (product_id, price_type, price_list_id) windows
    Business constraints: immutable; opening a new version closes the
        previous (effective_to = new.effective_from - 1tick); historical
        invoices read by effective date
    Classification: H (append-only)

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``discount.py`` (H3), ``price_list.py``
(C3), etc.): ``06_ERD.md`` is ``price_history``'s sole authority --
``price_history`` has no detailed section in ``07_DATABASE_SPEC.md``
(confirmed by search: the only ``07_DATABASE_SPEC.md`` mentions of
"price_history" are ``order_line``'s own deferred ``price_history_id`` FK
note (T11) -- no §-numbered ``price_history`` table section of its own).

Enum, ``06_ERD.md`` PART A::

    PriceType: RETAIL, REP, WHOLESALE, EXPORT, PROMO

Last deferred FK in the entire project -- retrofitting
``order_line.price_history_id``:
    ``price_history`` (H1) now exists, so the one remaining deferred-FK
    deviation in this codebase is resolved in the same change that creates
    this table: ``database/models/order_line.py`` (T11) already has a
    ``price_history_id`` column pointing here, currently a plain ``UUID``
    with no ``ForeignKey()`` (documented on that module as *"still
    deferred"* pending this table). See ``order_line.py``'s updated
    docstring for that retrofit's own notes -- this closes the same
    "table lands, deferred FK gets resolved" sequence already completed for
    ``app_user``, ``product_lot``, and ``discount``. After this change, a
    project-wide grep for the deferred-FK deviation phrase finds no
    remaining occurrences with an unresolved target table.

All three FKs are real from the outset:
    ``product``, ``price_list`` (C3, just built), and ``currency`` all
    already exist in this codebase, so ``product_id`` / ``price_list_id`` /
    ``currency_id`` are declared as real ``ForeignKey()`` constraints from
    the start -- no deferred-FK section to write for this table's own
    columns. None of the three carries a nullable annotation in the ERD's
    ``FK:`` line (contrast ``discount``'s FK line, where every one of its
    four FKs is explicitly marked ``(nullable)``), so all three are
    declared ``NOT NULL`` -- a price_history row is meaningless without a
    product, a price list, and a currency to price it in.

``price_type`` -- explicit ERD vocabulary, not an assumption:
    Bounded to ``PriceType`` (PART A): ``RETAIL`` / ``REP`` / ``WHOLESALE``
    / ``EXPORT`` / ``PROMO`` -- the same vocabulary ``price_list.price_type``
    already uses, with the same ``state_token_type()`` / CHECK treatment.

``unit_price`` -- type choice:
    ``money_type()`` -> ``NUMERIC(18, 4)``, the same factory
    ``order_line.unit_price`` / ``order.grand_total`` use for the same
    "actual money" semantics -- unlike ``discount.value``, this column has
    no dual PERCENT/AMOUNT interpretation to accommodate, just an ordinary
    selling price. ``NOT NULL`` -- the whole point of this row.

``effective_from`` / ``effective_to`` -- time-bounded validity:
    ``effective_from`` is ``NOT NULL`` (every version has a start of
    validity, and is part of the ``UniqueConstraint`` below).
    ``effective_to`` is explicitly marked ``(nullable)`` in the ERD's own
    ``Important fields:`` line -- the currently-open/latest version of a
    given ``(product_id, price_type, price_list_id)`` key has no known end
    yet. This is the same open-ended-validity shape as
    ``commission_config.effective_to`` / ``discount.valid_to``.

``is_promo`` / ``promo_valid_from`` / ``promo_valid_to`` -- promo-only
sub-window, nullable by logical necessity:
    ``is_promo`` is a plain ``Boolean``, ``NOT NULL DEFAULT false`` -- most
    price_history rows are ordinary (non-promotional) price versions.
    ``promo_valid_from`` / ``promo_valid_to`` are declared **nullable**
    even though the ERD's own ``Important fields:`` line gives them no
    explicit ``(nullable)`` annotation (unlike ``effective_to``, which
    *is* explicitly annotated) -- the same kind of logical-necessity
    override already applied to ``commission_config.effective_to`` /
    ``discount.valid_to`` (both nullable despite no explicit ERD marker,
    for the same "open-ended window" reasoning documented on those
    modules): a promo validity sub-window is only meaningful when
    ``is_promo = true``; an ordinary (non-promo) price version has no
    promo window to record at all, so ``NULL`` for both columns is the only
    sensible value on such rows, not a missing/unknown fact.

``reason`` -- free-text annotation, nullable:
    The ERD lists this as a bare field name ``reason`` with **no** ``→``
    arrow to a reference table (contrast ``inventory_transaction
    .reason_code_id -> reason_code_ref.id``, which *does* carry the arrow
    notation elsewhere in this same ERD/spec pair) -- so this is a free-text
    annotation of why this price version was opened (e.g. "seasonal
    adjustment", "competitor price match"), not a bounded vocabulary FK to
    ``reason_code_ref`` (R11). ``description_type()`` (``VARCHAR(255)``) is
    used, the same factory ``price_list.owner_scope`` uses for its own
    free-text field. Declared **nullable** -- unlike ``price_list
    .owner_scope`` (which is core to what a price list even means and is
    therefore ``NOT NULL``), ``reason`` here is an optional annotation on an
    otherwise fully-determined row (product/price/dates already say
    everything needed for pricing); many price versions -- especially
    routine/system-generated ones -- will have nothing to record here.

Unique constraint -- literal column list, an ordinary composite case (NOT
a naming trap):
    ``UniqueConstraint("product_id", "price_type", "price_list_id",
    "effective_from")`` via ``uq_index_name`` + ``composite_descriptor`` --
    the ERD gives this constraint's columns explicitly and literally
    (unlike ``discount``'s vague *"(scope_combo) per policy"*, or
    ``order_line`` / ``stock_reservation``'s bare-literal-name naming
    traps), so the standard helper output is used as-is with no override,
    the same ordinary treatment ``commission_config``'s own composite
    uniqueness already received.

Out of scope for this model (not implemented here):
    * *"No overlapping (product_id, price_type, price_list_id) windows"* --
      this is a temporal-range non-overlap rule across multiple rows
      sharing the same key, which PostgreSQL can only enforce via a
      range-type ``EXCLUDE`` constraint or a trigger -- not an ordinary
      column-level ``CHECK`` (which can only see one row at a time). Left
      to the application/service layer and/or a future migration-level
      ``EXCLUDE`` constraint, the same "cross-row/cross-table checks are a
      migration or service-layer concern" treatment already given to
      ``order_line``'s ``BEFORE UPDATE`` immutability trigger and
      ``stock_reservation``'s Sigma(reserved) <= available-balance check.
    * *"Opening a new version closes the previous (effective_to =
      new.effective_from - 1tick)"* -- a controlled, deterministic,
      exactly-once-per-row later ``UPDATE`` of the immediately-preceding
      row's own ``effective_to``, performed by the service layer at the
      moment the next version is inserted. This is the **same** shape of
      exception ``inventory_transaction.is_reversed`` already establishes
      as compatible with an append-only/AAC-classified table in this
      codebase: an append-only ledger row can still receive one
      deterministic, application-controlled mutation to a specific column
      later in its life (there: a reversal flips ``is_reversed`` from
      ``false`` to ``true``; here: closing a version sets ``effective_to``
      once) without contradicting the table's own "immutable, append-only"
      classification or its choice of audit mixin (see below) -- this is
      not something a CHECK/trigger enforces here, it is a documented
      service-layer write path.
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    The ERD's classification is unqualified ``"H (append-only)"`` --
    contrast ``discount``'s hybrid ``"C/H (mutable while draft, frozen once
    applied)"``, which used UAC precisely because a discount row is an
    ordinary mutable record before being referenced. ``price_history`` has
    no such "mutable while draft" phase at all: its own Business
    Constraints line states plainly *"immutable"*, and the ERD's own
    coverage-check line (E24) maps it directly to ``price_history`` under
    the ``H1`` label with no hybrid qualifier, matching
    ``inventory_transaction``'s own ``"T + H, immutable"`` classification
    (which also uses AAC). ``PriceHistory`` therefore gets ``created_at`` /
    ``created_by`` only -- no ``updated_at`` / ``updated_by`` / ``version``,
    and consequently no ``__mapper_args__ = {"version_id_col": ...}`` (AAC
    tables in this codebase do not opt into SQLAlchemy optimistic locking,
    since there is no second mutation to guard against beyond the single
    documented ``effective_to``-closing write described above).

Naming convention:
    All three FKs use ``fk_index_name`` normally
    (``fk_price_history_product_id_product_id``,
    ``fk_price_history_price_list_id_price_list_id``,
    ``fk_price_history_currency_id_currency_id``). The ``price_type``
    vocabulary CHECK uses ``ck_index_name`` normally ->
    ``ck_price_history_price_type_values``. The unique constraint uses
    ``uq_index_name`` + ``composite_descriptor`` as an ordinary composite
    case -- see the section above.

Column-type choices:

* ``price_type`` -- ``state_token_type()`` -> ``VARCHAR(16)``.
* ``unit_price`` -- ``money_type()`` -> ``NUMERIC(18, 4)``.
* ``effective_from`` / ``effective_to`` / ``promo_valid_from`` /
  ``promo_valid_to`` -- ``DateTime(timezone=True)``.
* ``is_promo`` -- ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT false``.
* ``reason`` -- ``description_type()`` -> ``VARCHAR(255)``, nullable.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import description_type, money_type, state_token_type


class PriceHistory(Base, AppendOnlyAuditColumns):
    """``H1 — price_history`` — immutable versioned selling price per (product, price_type, scope) (Classification: H, append-only)."""

    __tablename__ = "price_history"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------------- product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("price_history", "product_id", "product"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ price_list_id
    price_list_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "price_list.id",
            name=fk_index_name("price_history", "price_list_id", "price_list"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("price_history", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------- price_type
    # Explicit ERD vocabulary (PART A PriceType): RETAIL/REP/WHOLESALE/
    # EXPORT/PROMO. Same vocabulary as price_list.price_type.
    price_type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------------- unit_price
    unit_price: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- effective_from
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ---------------------------------------------------------------- effective_to
    # Explicitly marked (nullable) in the ERD -- open-ended for the
    # currently-open version.
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------------- is_promo
    is_promo: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    # --------------------------------------------------------- promo_valid_from
    # Nullable by logical necessity -- only meaningful when is_promo=true.
    # See module docstring's dedicated section.
    promo_valid_from: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ----------------------------------------------------------- promo_valid_to
    promo_valid_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ---------------------------------------------------------------------- reason
    # Free-text annotation, nullable -- see module docstring's dedicated
    # section (no "-> reason_code_ref" arrow given in the ERD, unlike
    # inventory_transaction.reason_code_id).
    reason: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- ordinary composite case, literal ERD column list. See
        # module docstring's "Unique constraint" section.
        UniqueConstraint(
            "product_id",
            "price_type",
            "price_list_id",
            "effective_from",
            name=uq_index_name(
                "price_history",
                composite_descriptor(
                    ["product_id", "price_type", "price_list_id", "effective_from"]
                ),
            ),
        ),
        # CHECK: price_type vocabulary.
        CheckConstraint(
            "price_type IN ('RETAIL', 'REP', 'WHOLESALE', 'EXPORT', 'PROMO')",
            name=ck_index_name("price_history", "price_type_values"),
        ),
    )


__all__ = ["PriceHistory"]
