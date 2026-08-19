"""``C1 — commission_config`` ORM model (commission rate configuration).

Authority: ``06_ERD.md``, PART C → ``C1 — commission_config``::

    C1 — commission_config
    Purpose: Commission rate configuration by representative / product
             category / order type, with time-bounded validity.
    PK: id
    FK: representative_id → representative (nullable for global default),
        product_category_id → product_category (nullable)
    Important fields: order_type (OrderType), rate, effective_from,
                      effective_to
    Unique: (representative_id, product_category_id, order_type,
             effective_from)
    Classification: C

Same gap as every other table with no dedicated spec section so far:
``06_ERD.md`` is C1's sole authority — C1 has no detailed section in
``07_DATABASE_SPEC.md``.

Enum, ``06_ERD.md`` PART A::

    OrderType: LOCAL, DIRECT

Relationship to ``representative.commission_config_id``:
    ``database/models/representative.py`` (M6) already has a
    ``commission_config_id`` column pointing here, currently a plain
    ``UUID`` with no ``ForeignKey()`` because this table did not exist yet
    (the same deferred-FK deviation used throughout this codebase). This
    task only creates ``commission_config`` itself; retrofitting
    ``representative.commission_config_id`` into a real ``ForeignKey()`` is
    a separate follow-up task, not part of this change.

``representative_id`` — nullable FK, "global default":
    ``representative_id → representative``, nullable. The ERD's own
    parenthetical on this exact field — "nullable for global default" — is
    the reason: a ``NULL`` ``representative_id`` denotes a commission
    configuration that applies globally (not scoped to one representative),
    rather than a missing/unknown value. This is the same parenthetical
    already cited from the representative (M6) side when
    ``representative.py`` described why its own ``commission_config_id`` is
    optional.

``product_category_id`` — nullable FK:
    ``product_category_id → product_category``, nullable — the ERD marks
    this nullable explicitly (presumably for the same "no category = applies
    across all categories" default reasoning as ``representative_id``,
    though the ERD's explicit nullability note is transcribed here without
    inventing further justification beyond what the ERD states).

``order_type`` — explicit ERD vocabulary, not an assumption:
    Bounded to ``OrderType`` (PART A): ``LOCAL`` / ``DIRECT``. Like
    ``representative.status``, this vocabulary is given directly in the ERD
    text, not assumed the way ``carrier.py`` / ``warehouse.py`` /
    ``app_user.py`` had to assume their own unspecified ``status``
    vocabularies.

``rate`` — type choice AND the 0..100 bound:
    ``rate_type()`` → ``NUMERIC(7, 4)``, matching ``database.types``'
    percentage-rate factory (already used for ``invoice_line.tax_rate`` /
    ``commission_transaction.rate_applied`` per that factory's own
    docstring — ``commission_config.rate`` is squarely in that family).
    ``rate_type()``'s own docstring is explicit that the 0..100 bound "is
    expressed as a ``CHECK`` constraint on the model, not here" — so this
    model adds ``CheckConstraint("rate >= 0 AND rate <= 100", ...)`` named
    via ``ck_index_name`` → ``ck_commission_config_rate_range``, the same
    treatment ``warehouse.py`` gives its own out-of-factory latitude /
    longitude range CHECKs.

``effective_from`` / ``effective_to`` — time-bounded validity:
    ``effective_from`` is ``NOT NULL`` — every commission_config row has a
    start of validity. ``effective_to`` is nullable, representing
    open-ended validity (a configuration that has not yet been superseded /
    has no scheduled end date).

Soft delete — deliberately absent:
    Unlike ``product.py`` (M1), ``warehouse.py`` (M4), and ``app_user.py``
    (M10) — all classified "M + soft-deletable" — the ERD classifies
    ``commission_config`` as plain ``C``, with no "+ soft-deletable"
    qualifier. No ``deleted_at`` column is declared here. This is a
    deliberate difference from the M-tables' soft-delete pattern, not an
    oversight: a commission_config row's own ``effective_from`` /
    ``effective_to`` window already expresses its lifecycle (a row simply
    stops being the applicable configuration once ``effective_to`` passes,
    or once superseded by a later row), so there is no separate
    soft-delete concept layered on top.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``CommissionConfig`` uses UAC and opts its ``version``
    column into SQLAlchemy optimistic locking (``__mapper_args__ =
    {"version_id_col": "version"}``), exactly like every other model in this
    codebase so far.

Uniqueness:
    ``UniqueConstraint(representative_id, product_category_id, order_type,
    effective_from)`` via ``uq_index_name`` + ``composite_descriptor`` — an
    ordinary composite-descriptor case, the same treatment
    ``product_lot.py`` gives its own
    ``(product_id, lot_code)`` composite uniqueness, unlike
    ``inventory_transaction``'s spec-literal constraint-naming overrides.

Naming convention:
    ``representative_id`` / ``product_category_id`` use ``fk_index_name`` →
    ``fk_commission_config_representative_id_representative_id`` /
    ``fk_commission_config_product_category_id_product_category_id`` (the
    trailing ``_id`` is ``fk_index_name``'s default
    ``referred_column_name="id"``, appended after the referred table name —
    verified by actually calling ``fk_index_name``, not assumed from the
    table name alone). The
    ``order_type`` vocabulary is bounded by a CHECK named via
    ``ck_index_name`` → ``ck_commission_config_order_type_values``. The
    ``rate`` bound uses ``ck_index_name`` →
    ``ck_commission_config_rate_range``. The composite uniqueness uses
    ``uq_index_name`` + ``composite_descriptor``; the literal concatenation
    ``uq_commission_config_representative_id_product_category_id_order_type_effective_from``
    is 87 characters — over PostgreSQL's 63-char identifier limit — so
    ``uq_index_name``'s own ``_enforce_length_limit`` guard (documented in
    ``database/naming.py``) deterministically shortens it to
    ``uq_commission_config_representative_id_product_categor_7b6d1760``
    (truncated prefix + an 8-hex-char SHA-256 suffix), not the full literal
    name. This was verified by actually running ``uq_index_name`` against
    this table/column combination, not assumed.

Column-type choices:

* ``order_type`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``LOCAL`` / ``DIRECT``.
* ``rate`` — ``rate_type()`` → ``NUMERIC(7, 4)`` (see note above).
* ``effective_from`` / ``effective_to`` — ``DateTime(timezone=True)``.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import rate_type, state_token_type


class CommissionConfig(Base, UniversalAuditColumns):
    """``C1 — commission_config`` — commission rate configuration (Classification: C)."""

    __tablename__ = "commission_config"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------ representative_id
    # NULL = global default (see module docstring's ERD parenthetical).
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("commission_config", "representative_id", "representative"),
        ),
        nullable=True,
    )

    # --------------------------------------------------- product_category_id
    # ERD marks this nullable explicitly.
    product_category_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_category.id",
            name=fk_index_name("commission_config", "product_category_id", "product_category"),
        ),
        nullable=True,
    )

    # ---------------------------------------------------------- order_type
    # Explicit ERD vocabulary (PART A OrderType): LOCAL / DIRECT.
    order_type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ---------------------------------------------------------------- rate
    # rate_type() covers precision/scale only; the 0..100 bound is enforced
    # below via CHECK, per rate_type()'s own docstring instruction.
    rate: Mapped[decimal.Decimal] = mapped_column(
        rate_type(),
        nullable=False,
    )

    # -------------------------------------------------------- effective_from
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ---------------------------------------------------------- effective_to
    # Nullable — open-ended validity.
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "representative_id",
            "product_category_id",
            "order_type",
            "effective_from",
            name=uq_index_name(
                "commission_config",
                composite_descriptor(
                    ["representative_id", "product_category_id", "order_type", "effective_from"]
                ),
            ),
        ),
        CheckConstraint(
            "order_type IN ('LOCAL', 'DIRECT')",
            name=ck_index_name("commission_config", "order_type_values"),
        ),
        CheckConstraint(
            "rate >= 0 AND rate <= 100",
            name=ck_index_name("commission_config", "rate_range"),
        ),
    )


__all__ = ["CommissionConfig"]
