"""``H3 — discount`` ORM model (defined discounts, scoped + time-bounded).

Authority: ``06_ERD.md``, line 46 → ``H3 — discount``::

    H3 — discount
    Purpose: Defined discounts scoped to product/category/customer/rep
             with validity window.
    PK: id
    FK: product_id → product (nullable), category_id → product_category
        (nullable), customer_id → customer (nullable),
        representative_id → representative (nullable)
    Important fields: discount_type (DiscountType), value, valid_from,
                      valid_to, scope_tag
    Unique: (scope_combo) per policy
    Business constraints: promo-style discounts never applied
                          retroactively; stackability flagged
    Classification: C/H (mutable while draft, frozen once applied to an
                    order/invoice)

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``bot_platform_ref.py`` (R12), etc.):
``06_ERD.md`` is ``discount``'s sole authority — ``discount`` has no
detailed section in ``07_DATABASE_SPEC.md`` (confirmed by search: the only
``07_DATABASE_SPEC.md`` mentions of "discount" are ``order`` /
``order_line`` / ``invoice``'s own ``discount_total`` / ``discount_value``
cache columns and ``order_line``'s deferred ``discount_id`` FK note — none
of them a §-numbered ``discount`` table section of its own).

Enum, ``06_ERD.md`` PART A::

    DiscountType: PERCENT, AMOUNT

Relationship to ``order_line.discount_id``:
    ``database/models/order_line.py`` (T11) already has a ``discount_id``
    column pointing here, currently a plain ``UUID`` with no
    ``ForeignKey()`` because this table did not exist yet (the same
    deferred-FK deviation used throughout this codebase — see that
    module's own "Deferred-FK deviation" docstring section). This task
    creates ``discount`` itself AND retrofits ``order_line.discount_id``
    into a real ``ForeignKey("discount.id")`` in the same change (see
    ``order_line.py``'s updated docstring for that retrofit's own notes) --
    the same "table lands, deferred FK gets resolved" sequence already
    completed for ``app_user`` (mixins/``warehouse.responsible_user_id``/
    ``inventory_transaction.actor_user_id``) and ``product_lot``
    (``inventory_transaction.lot_id``).

Four nullable FKs -- all "nullable = broader scope", not "unknown":
    Every one of ``product_id`` / ``category_id`` / ``customer_id`` /
    ``representative_id`` is nullable per the ERD, and every one follows the
    same reasoning already established for ``commission_config``'s own
    nullable scope FKs (``representative_id``: *"nullable for global
    default"*): a ``NULL`` here means "this discount is not scoped to a
    specific product / category / customer / representative", not a missing
    value. A single discount row can combine several non-``NULL`` scope
    columns at once (e.g. a discount scoped to both a specific
    ``customer_id`` AND a specific ``product_id`` simultaneously) -- the ERD
    describes the discount as *"scoped to product/category/customer/rep"*
    (all four, not "exactly one of"), so no mutual-exclusivity CHECK is
    added between the four scope columns; any combination of NULLs/values
    across them is valid schema-wise, with the business layer interpreting
    the combination.

``value`` -- ``money_type()``, NOT ``rate_type()``, despite ``PERCENT``
being a valid ``discount_type``:
    ``rate_type()`` (``NUMERIC(7, 4)``, used by ``commission_config.rate`` /
    ``invoice_line.tax_rate``) is scoped to percentage-shaped values and
    tops out at ``999.9999`` -- fine for a rate, but ``value`` on this table
    must ALSO represent an absolute currency ``AMOUNT`` when
    ``discount_type = 'AMOUNT'``, and an absolute discount amount can
    legitimately exceed a 3-integer-digit ceiling. ``money_type()``
    (``NUMERIC(18, 4)``, the same factory ``order_line.unit_price`` /
    ``order.grand_total`` use) is therefore the correct shape here: wide
    enough for either interpretation, with the ``discount_type``-conditional
    ``0..100`` bound enforced by CHECK only when ``discount_type =
    'PERCENT'`` (see the CHECK below) rather than baked into the column's
    own precision/scale the way ``commission_config.rate`` bakes it in
    unconditionally.

``scope_tag`` -- type-width choice:
    The ERD lists this as an "important field" alongside ``discount_type``/
    ``value``/``valid_from``/``valid_to`` with no explicit width given.
    ``code_short_type()`` (``VARCHAR(40)``) is used here as the closest
    existing factory for a short business-facing label/tag -- the same
    "closest existing factory, not a bespoke new type" treatment
    ``order.py`` / ``order_line.py`` already gave their own
    ``sales_channel`` / ``fulfillment_mode`` placeholder-width columns.
    Declared ``NOT NULL`` -- the ERD gives no nullable annotation for it
    (unlike the four FK columns, each explicitly marked ``(nullable)`` in
    the same ERD line), so it is treated as required like ``discount_type``/
    ``value``/``valid_from``.

``valid_from`` / ``valid_to`` -- time-bounded validity:
    Same pattern as ``commission_config.effective_from`` /
    ``effective_to``: ``valid_from`` is ``NOT NULL`` (every discount has a
    start of validity); ``valid_to`` is nullable, representing open-ended
    validity (a discount with no scheduled end date) -- the ERD gives no
    explicit nullable annotation for either column (same terse style as
    ``commission_config``'s own ERD line), so this follows the identical
    precedent already established there rather than inventing a new
    convention for this table.

Unique constraint -- deliberately NOT implemented as a schema-level
``UniqueConstraint``:
    The ERD's own wording is ``"Unique: (scope_combo) per policy"`` --
    unlike every other table's ``Unique:`` line in this ERD/spec (which
    gives a literal, concrete column list -- e.g. ``order_number``,
    ``(order_id, product_id, lot_id)``, ``(representative_id,
    product_category_id, order_type, effective_from)``), this one names no
    concrete column list and explicitly defers to "policy" -- i.e. a
    business rule, not a fixed set of columns a SQL ``UNIQUE`` could
    enforce. This reading is reinforced by the four scope columns all being
    independently nullable with no stated mutual-exclusivity rule (see
    above): "the same scope combination" is not well-defined as a flat
    column tuple once several of ``product_id``/``category_id``/
    ``customer_id``/``representative_id`` can be simultaneously non-NULL in
    ways the ERD does not enumerate, and the "stackability flagged" business
    constraint further implies that intentionally-overlapping-scope
    discounts are an expected, not a forbidden, case. Fabricating a literal
    ``UniqueConstraint`` over an unspecified column list here would invent a
    hard schema rule the ERD explicitly declines to specify — so none is
    added; "scope_combo" uniqueness is left to the application/service layer
    ("per policy"), consistent with the "out of scope for this model" cross-
    table/business-layer checks already documented on ``order_line.py`` /
    ``stock_reservation.py`` (e.g. the ``BEFORE UPDATE`` immutability
    trigger, the Sigma(reserved) <= available-balance check).

Naming convention:
    All four FKs use ``fk_index_name`` normally (``fk_discount_product_id_
    product_id``, ``fk_discount_category_id_product_category_id``,
    ``fk_discount_customer_id_customer_id``,
    ``fk_discount_representative_id_representative_id``). Both CHECKs use
    ``ck_index_name`` normally: ``ck_discount_discount_type_values``,
    ``ck_discount_percent_value_range``. There is no ``UniqueConstraint`` --
    see the section above.

Column-type choices:

* ``discount_type`` -- ``state_token_type()`` -> ``VARCHAR(16)``, an exact
  fit for the two-member ``DiscountType`` vocabulary (``PERCENT`` /
  ``AMOUNT``), the same factory ``commission_config.order_type`` /
  ``stock_reservation.state`` already use for short bounded vocabularies.
* ``value`` -- ``money_type()`` -> ``NUMERIC(18, 4)`` (see dedicated note
  above).
* ``valid_from`` / ``valid_to`` -- ``DateTime(timezone=True)``, matching
  ``commission_config.effective_from`` / ``effective_to``.
* ``scope_tag`` -- ``code_short_type()`` -> ``VARCHAR(40)`` (see dedicated
  note above).

Soft delete -- deliberately absent, same reasoning as ``commission_config``:
    The ERD classifies ``discount`` as plain ``C/H`` with no
    "+ soft-deletable" qualifier (unlike ``product.py`` / ``warehouse.py`` /
    ``app_user.py``'s explicit "M + soft-deletable" classification). No
    ``deleted_at`` column is declared. A discount's own ``valid_from`` /
    ``valid_to`` window already expresses its lifecycle the same way
    ``commission_config.effective_from``/``effective_to`` does -- a row
    simply stops applying once ``valid_to`` passes, with no separate
    soft-delete concept layered on top.

Audit-column family -- ``UniversalAuditColumns`` (UAC), not AAC:
    The ERD's own classification parenthetical -- *"mutable while draft,
    frozen once applied to an order/invoice"* -- describes an ordinary
    mutable business record (like ``commission_config``, which also uses
    UAC), not an append-only/immutable-once-written history row (AAC's
    domain, e.g. ``shipment_status_history``). The "frozen once applied"
    behavior is NOT modeled as immutability on this table's own rows --
    it is already fully modeled on the *consumer* side: ``order_line``'s
    own ``discount_id`` / ``discount_value`` columns are the snapshot/frozen
    copy (per that table's own docstring and its cross-table ``BEFORE
    UPDATE`` immutability trigger note), while this ``discount`` row itself
    remains an ordinary mutable UAC-audited record up until and after being
    referenced. ``CommissionConfig`` uses UAC and opts its ``version``
    column into SQLAlchemy optimistic locking (``__mapper_args__ =
    {"version_id_col": "version"}``); this model does the same.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import code_short_type, money_type, state_token_type


class Discount(Base, UniversalAuditColumns):
    """``H3 — discount`` — defined discounts scoped to product/category/customer/rep, with a validity window (Classification: C/H)."""

    __tablename__ = "discount"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------- product_id
    # Nullable = "not scoped to a specific product" (see module docstring's
    # "Four nullable FKs" section).
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("discount", "product_id", "product"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------ category_id
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_category.id",
            name=fk_index_name("discount", "category_id", "product_category"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------ customer_id
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("discount", "customer_id", "customer"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------ representative_id
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("discount", "representative_id", "representative"),
        ),
        nullable=True,
    )

    # ---------------------------------------------------------- discount_type
    # Explicit ERD vocabulary (PART A DiscountType): PERCENT / AMOUNT.
    discount_type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------- value
    # money_type(), NOT rate_type() -- see module docstring's dedicated
    # note on why. The 0..100 bound for PERCENT rows is enforced below via
    # a discount_type-conditional CHECK, not baked into the column type.
    value: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- valid_from
    valid_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ---------------------------------------------------------------- valid_to
    # Nullable -- open-ended validity, same precedent as
    # commission_config.effective_to.
    valid_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------------------------------------------------- scope_tag
    scope_tag: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
    )

    __table_args__ = (
        # CHECK: discount_type vocabulary.
        CheckConstraint(
            "discount_type IN ('PERCENT', 'AMOUNT')",
            name=ck_index_name("discount", "discount_type_values"),
        ),
        # CHECK: value non-negative, and additionally bounded to 0..100
        # when (and only when) discount_type = 'PERCENT' -- an AMOUNT-type
        # discount has no such upper bound.
        CheckConstraint(
            "value >= 0 AND (discount_type <> 'PERCENT' OR value <= 100)",
            name=ck_index_name("discount", "percent_value_range"),
        ),
    )


__all__ = ["Discount"]
