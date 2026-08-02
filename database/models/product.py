"""``M1 — product`` ORM model (core product master data).

Authority: ``06_ERD.md``, M1 → ``product``::

    M1 — product
    Purpose: Product definition (SRS E1, BRF).
    PK: id
    FK: category_id → product_category, base_uom_id → unit_of_measure
    Important fields: sku (unique), name, description, is_lot_tracked (bool),
                      is_serial_tracked (bool), is_perishable (bool),
                      shelf_life_days (nullable), status (ACTIVE/DISCONTINUED),
                      variant_of_id → product (nullable, self-ref for variant parent)
    Unique: sku
    Business constraints: SKU immutable once shipped against; discontinued
                          products block new orders but remain visible historically;
                          if variant_of_id null → it's a standalone or parent product
    Classification: M + soft-deletable

``06_ERD.md`` is M1's sole authority: M1 has no detailed section in
``07_DATABASE_SPEC.md``. The ERD does not mark ``category_id`` as required,
so it is nullable. ``variant_of_id`` is also nullable and follows the existing
unqualified self-reference pattern, ``ForeignKey("product.id", ...)``.

Soft delete:
    No reusable soft-delete mixin is relied on here. As the first soft-deletable
    model, ``deleted_at`` is declared directly: a nullable timezone-aware
    timestamp whose default NULL denotes a product that has not been deleted.

SKU immutability after shipment, discontinued-product order blocking, and the
standalone/parent interpretation of a NULL ``variant_of_id`` are service-layer
business rules, not row-local SQL CHECKs.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Product`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking.

Naming convention:
    ``sku`` uses column-level ``unique=True``, which renders as
    ``uq_product_sku``. The three FK constraints use ``fk_index_name`` and the
    bounded status vocabulary uses ``ck_index_name`` →
    ``ck_product_status_values``.

Column-type choices:

* ``sku`` — ``code_short_type()`` → ``VARCHAR(40)`` for the short,
  controlled stock-keeping code.
* ``name`` — ``name_type()`` → ``VARCHAR(160)`` for the display name.
* ``description`` — ``description_type()`` → ``VARCHAR(255)``.
* ``status`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``ACTIVE`` / ``DISCONTINUED``.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import code_short_type, description_type, name_type, state_token_type


class Product(Base, UniversalAuditColumns):
    """``M1 — product`` — core product master data (Classification: M + soft-deletable)."""

    __tablename__ = "product"
    __mapper_args__ = {"version_id_col": "version"}

    id: GuidPk = id_column()

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_category.id",
            name=fk_index_name("product", "category_id", "product_category"),
        ),
        nullable=True,
    )
    base_uom_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "unit_of_measure.id",
            name=fk_index_name("product", "base_uom_id", "unit_of_measure"),
        ),
        nullable=False,
    )
    variant_of_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("product", "variant_of_id", "product"),
        ),
        nullable=True,
    )

    sku: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )

    # Defaults intentionally mirror Currency.is_base.
    is_lot_tracked: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=sa_text("false")
    )
    is_serial_tracked: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=sa_text("false")
    )
    is_perishable: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=sa_text("false")
    )
    shelf_life_days: Mapped[int | None] = mapped_column(
        Integer(),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # Direct, opt-in soft-delete marker; NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'DISCONTINUED')",
            name=ck_index_name("product", "status_values"),
        ),
    )


__all__ = ["Product"]
