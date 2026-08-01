"""``M2 — product_lot`` ORM model (batch/lot records for traceability).

Authority: ``06_ERD.md``, PART C → ``M2 — product_lot``::

    M2 — product_lot
    Purpose: Batch/lot records for traceability, FEFO, expiry, cost binding
             (BRF §6).
    PK: id
    FK: product_id → product
    Important fields: lot_code (unique per product), manufactured_at,
                      expires_at, status (LotStatus), initial_quantity
    Unique: (product_id, lot_code)
    Business constraints: expired/quarantine lots blocked from sale
                          transactions; expedited by expires_at
    Classification: M (lots are created and live until fully consumed/expired)

Enum, ``06_ERD.md`` PART A::

    LotStatus: SALEABLE, DAMAGED, EXPIRED, QUARANTINE

``06_ERD.md`` is M2's sole authority: like every other M-table so far, M2 has
no detailed section in ``07_DATABASE_SPEC.md`` — this docstring cites the ERD
line only, mirroring the same documented gap already noted on ``product.py``
(M1) and ``carrier.py`` (R14).

Uniqueness — NOT globally unique:
    The ERD states ``lot_code (unique per product)``, not a global unique
    value. Enforcing this with column-level ``unique=True`` on ``lot_code``
    (the pattern used by ``product.sku`` and ``carrier.code``) would be wrong
    here — it would forbid the same ``lot_code`` from ever appearing under two
    different products, which is not what "unique per product" means. Instead
    this is enforced with a composite ``UniqueConstraint(product_id, lot_code)``
    via ``uq_index_name`` + ``composite_descriptor`` — an ordinary composite
    descriptor case, unlike ``inventory_transaction``'s spec-literal
    constraint-naming overrides; the helpers are used exactly as documented in
    ``database/naming.py``.

Soft delete — deliberately absent:
    Unlike ``product.py`` (M1, "M + soft-deletable") and ``warehouse.py`` (M4,
    "M + soft-deletable"), the ERD classifies ``product_lot`` as plain ``M`` —
    no "+ soft-deletable" qualifier. No ``deleted_at`` column is declared here.
    This is a deliberate difference from the M1/M4 pattern, not an oversight:
    a lot is a batch-identity record that is "created and live until fully
    consumed/expired" per the ERD's own classification note, so its lifecycle
    is expressed through ``status`` (moving into ``EXPIRED`` /
    ``QUARANTINE`` / ``DAMAGED``) rather than through soft-delete semantics.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``ProductLot`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking, matching ``Carrier`` and ``Product``.

Business constraints (docstring only — no SQL):
    Expired and quarantine lots are blocked from participating in sale
    transactions. This is a cross-table, temporal rule (it depends on
    ``status`` at the moment a *different* table's row — an order/shipment
    line — is posted, not on any value fixed within this row alone), so it
    cannot be expressed as a row-local SQL ``CHECK`` here. It is enforced at
    the service layer when sale-related inventory transactions are posted.

Naming convention:
    ``status`` is bounded to the ERD's ``LotStatus`` vocabulary by
    ``ck_index_name`` → ``ck_product_lot_status_values``, mirroring
    ``Carrier.status`` / ``Product.status``. ``product_id`` uses
    ``fk_index_name`` → ``fk_product_lot_product_id_product_id``, mirroring
    ``Product.category_id`` / ``Product.base_uom_id``. The composite
    uniqueness uses ``uq_index_name`` + ``composite_descriptor`` →
    ``uq_product_lot_product_id_lot_code``.

Column-type choices:

* ``lot_code`` — ``code_short_type()`` → ``VARCHAR(40)``, the same short
  controlled-code width used by ``product.sku`` and ``carrier.code``.
* ``manufactured_at`` / ``expires_at`` — ``DateTime(timezone=True)``,
  nullable: the ERD does not mark either as required.
* ``status`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``SALEABLE`` / ``DAMAGED`` / ``EXPIRED`` / ``QUARANTINE``.
* ``initial_quantity`` — ``money_type()`` → ``NUMERIC(18, 4)``. Its own
  docstring in ``database/types.py`` covers ``qty_*`` columns generally
  ("money totals & signed transaction quantities... every ``signed_quantity``
  / ``qty_*`` column"), which ``initial_quantity`` falls under.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import code_short_type, money_type, state_token_type


class ProductLot(Base, UniversalAuditColumns):
    """``M2 — product_lot`` — batch/lot records for traceability (Classification: M)."""

    __tablename__ = "product_lot"
    __mapper_args__ = {"version_id_col": "version"}

    id: GuidPk = id_column()

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "product.id",
            name=fk_index_name("product_lot", "product_id", "product"),
        ),
        nullable=False,
    )

    # Unique per product, NOT globally unique — enforced below via the
    # composite UniqueConstraint, not column-level unique=True.
    lot_code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
    )

    # ERD does not mark either as required.
    manufactured_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ERD LotStatus: SALEABLE, DAMAGED, EXPIRED, QUARANTINE.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    initial_quantity: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "lot_code",
            name=uq_index_name(
                "product_lot",
                composite_descriptor(["product_id", "lot_code"]),
            ),
        ),
        CheckConstraint(
            "status IN ('SALEABLE', 'DAMAGED', 'EXPIRED', 'QUARANTINE')",
            name=ck_index_name("product_lot", "status_values"),
        ),
    )


__all__ = ["ProductLot"]
