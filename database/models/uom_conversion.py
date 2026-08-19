"""``R3 — uom_conversion`` ORM model (conversion factors between UoMs).

Authority: ``06_ERD.md``, PART B → ``R3 — uom_conversion``::

    R3 — uom_conversion
    Purpose: Conversion factors between UoMs per product or globally.
    PK: id
    FK: from_uom_id → unit_of_measure, to_uom_id → unit_of_measure,
        optional product_id → product
    Important fields: factor (numeric)
    Unique: (from_uom_id, to_uom_id, product_id)
    Classification: R

Same gap as every other table with no dedicated spec section so far:
``06_ERD.md`` is R3's sole authority — R3 has no detailed section in
``07_DATABASE_SPEC.md``.

Previously blocked, now unblocked:
    This entity was previously deferred because ``product`` (M1) did not
    exist. ``product`` has since landed (``database/models/product.py``),
    so ``product_id`` is built here as a real ``ForeignKey()`` from the
    start — there is no deferred-FK deviation on this model at all, unlike
    ``app_user.representative_id`` / ``warehouse.responsible_user_id`` at
    the time they were first written.

``from_uom_id`` / ``to_uom_id`` — required FKs:
    Both ``→ unit_of_measure`` (R2), ``NOT NULL``. The ERD's ``FK:`` line
    lists these two without a nullability qualifier, unlike its own
    explicit "optional" on ``product_id`` (see below) — the presence of
    that qualifier on only one of the three FKs is read as the ERD marking
    exactly one of them optional and the other two required, rather than
    all three sharing the same nullability by omission.

``product_id`` — nullable, "optional", global-default reasoning:
    The ERD's own word for this FK is "optional" — nullable. A ``NULL``
    ``product_id`` denotes a conversion factor that applies globally across
    all products, rather than a missing/unknown value. This is the same
    NULL-means-global-default reasoning already used for
    ``commission_config.representative_id`` / ``.product_category_id``.
    ``product`` (M1) already exists in this codebase, so this is a real
    ``ForeignKey()``.

``factor`` — placeholder column-type choice:
    No dedicated column-type factory exists in ``database/types.py`` for a
    conversion ratio: it is not money, not a percentage/rate bounded 0..100
    (``rate_type()``), and not itself a cost. ``cost_type()`` →
    ``NUMERIC(18, 6)`` is used as the closest existing factory by
    precision/scale (six decimal places accommodates conversion ratios that
    are not round numbers, e.g. non-integer box-to-each factors) — flagged
    here as a placeholder choice, the same "closest existing factory, not a
    considered fit" treatment ``warehouse.py`` gives ``address``
    (``description_type()``) and ``app_user.py`` gives ``password_hash``
    (``token_type()``), rather than inventing a new ``types.py`` factory
    unprompted.

Uniqueness:
    ``UniqueConstraint(from_uom_id, to_uom_id, product_id)`` via
    ``uq_index_name`` + ``composite_descriptor`` — an ordinary
    composite-descriptor case, the same treatment ``product_lot.py`` and
    ``commission_config.py`` give their own composite uniqueness, unlike
    ``inventory_transaction``'s spec-literal constraint-naming overrides.

``factor > 0`` — an INFERRED invariant, not an ERD-stated one:
    The ERD does not literally state a positivity bound on ``factor``. This
    model adds ``CheckConstraint("factor > 0", ...)`` anyway, because a
    zero or negative conversion factor is physically meaningless (it would
    mean "N units of the ``from`` UoM equal zero or a negative quantity of
    the ``to`` UoM", which cannot correspond to any real unit relationship).
    This is flagged explicitly as an inferred invariant this model adds by
    judgment, not a value transcribed from the ERD text — the same
    treatment ``inventory_transaction``'s own inferred CHECKs receive
    relative to its spec-literal ones. Everything else in this model's
    ``__table_args__`` (the composite uniqueness) is ERD-derived; this one
    CHECK is the sole judgment addition.

No soft delete:
    Classification is plain ``R`` — the same as ``currency`` (R5),
    ``unit_of_measure`` (R2), ``product_category`` (R1), ``role`` (R6),
    and most other reference tables so far. No ``deleted_at`` column is
    declared; ``UniversalAuditColumns`` (below) already carries no
    ``deleted_at``, so the mixin is used as-is.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``UomConversion`` uses UAC and opts its ``version`` column
    into SQLAlchemy optimistic locking (``__mapper_args__ =
    {"version_id_col": "version"}``), exactly like every other model in
    this codebase so far.

Naming convention:
    ``from_uom_id`` / ``to_uom_id`` / ``product_id`` all use
    ``fk_index_name`` →
    ``fk_uom_conversion_from_uom_id_unit_of_measure_id`` /
    ``fk_uom_conversion_to_uom_id_unit_of_measure_id`` /
    ``fk_uom_conversion_product_id_product_id`` (the trailing ``_id`` on
    each is ``fk_index_name``'s default ``referred_column_name="id"``,
    appended after the referred table name — verified below by actually
    calling ``fk_index_name``, not assumed). The composite uniqueness uses
    ``uq_index_name`` + ``composite_descriptor``; the ``factor`` CHECK uses
    ``ck_index_name`` → ``ck_uom_conversion_factor_positive``.

Column-type choices:

* ``factor`` — ``cost_type()`` → ``NUMERIC(18, 6)`` (placeholder — see
  note above).
"""

from __future__ import annotations

import decimal
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import cost_type


class UomConversion(Base, UniversalAuditColumns):
    """``R3 — uom_conversion`` — conversion factors between UoMs (Classification: R)."""

    __tablename__ = "uom_conversion"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------- from_uom_id
    from_uom_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "unit_of_measure.id",
            name=fk_index_name("uom_conversion", "from_uom_id", "unit_of_measure"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- to_uom_id
    to_uom_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "unit_of_measure.id",
            name=fk_index_name("uom_conversion", "to_uom_id", "unit_of_measure"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- product_id
    # ERD: "optional product_id" — NULL means the conversion applies
    # globally across all products (see module docstring).
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("uom_conversion", "product_id", "product"),
        ),
        nullable=True,
    )

    # ---------------------------------------------------------------- factor
    # Placeholder column-type choice — see module docstring.
    factor: Mapped[decimal.Decimal] = mapped_column(
        cost_type(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "from_uom_id",
            "to_uom_id",
            "product_id",
            name=uq_index_name(
                "uom_conversion",
                composite_descriptor(["from_uom_id", "to_uom_id", "product_id"]),
            ),
        ),
        # INFERRED, not ERD-stated — see module docstring.
        CheckConstraint(
            "factor > 0",
            name=ck_index_name("uom_conversion", "factor_positive"),
        ),
    )


__all__ = ["UomConversion"]
