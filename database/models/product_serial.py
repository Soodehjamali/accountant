"""``M3 — product_serial`` ORM model (unit-level serial tracking, optional per product).

Authority: ``06_ERD.md``, line 25 → ``M3 — product_serial``::

    M3 — product_serial
    Purpose: Unit-level serial tracking (optional per product).
    PK: id
    FK: product_id → product, lot_id → product_lot (nullable)
    Important fields: serial_number (unique), status (IN_STOCK, SOLD,
                      RETURNED, DAMAGED)
    Unique: serial_number
    Classification: M

``06_ERD.md`` is M3's sole authority: like every other M-table so far
(``product.py`` (M1), ``product_lot.py`` (M2)), M3 has no detailed section in
``07_DATABASE_SPEC.md`` — this docstring cites the ERD line only.

Enum, inline in the ERD's ``Important fields:`` line (M3 has no dedicated
``PART A`` block entry the way ``LotStatus``/``CostingMethod`` do — the four
members are spelled out directly in the M3 line itself)::

    status: IN_STOCK, SOLD, RETURNED, DAMAGED

``product_id`` — ``ForeignKey("product.id")``, ``NOT NULL``:
    ``product`` already exists in this codebase. The ERD's ``FK:`` line
    marks only ``lot_id`` as ``(nullable)``, implying ``product_id`` is not —
    every serial-tracked unit belongs to exactly one product, the same
    "every serial has a product, but not every serial has a lot" split the
    Purpose line's "unit-level ... tracking" already implies.

``lot_id`` — ``ForeignKey("product_lot.id")``, nullable, per direct ERD
annotation:
    ``product_lot`` already exists in this codebase. The ERD explicitly
    parenthesizes ``lot_id → product_lot (nullable)`` — a serialized unit may
    or may not be bound to a specific lot/batch, mirroring
    ``product.is_lot_tracked``'s own per-product opt-in: lot-tracking and
    serial-tracking are independent axes, so a serial row can exist without
    a lot association even on a lot-tracked product (e.g. before the unit is
    assigned to a specific batch), or trivially on a product that is not
    lot-tracked at all.

``serial_number`` — globally unique physical-unit identifier:
    ``business_key_type()`` -> ``VARCHAR(40)``, the factory whose own
    docstring explicitly frames itself around "``*_number`` columns"
    (order/transfer/invoice/payment/credit-note/adjustment/return/shipment
    business-key ``*_number`` columns) — the closest existing semantic match
    for a ``*_number``-suffixed identifier column, chosen over
    ``code_short_type()`` (the factory ``product.sku`` uses) precisely
    because ``code_short_type()``'s own docstring frames itself around
    *codes* (SKU / warehouse code / ISO-3), a materially different naming
    family from a ``_number`` business-key column, even though both factories
    happen to share the same 40-char width. ``NOT NULL`` — a serial-tracked
    unit with no serial number is not a meaningful row — and given
    column-level ``unique=True`` per the ERD's own ``Unique: serial_number``
    line.

``status`` — explicit ERD vocabulary, CHECK-bounded, same idiom as
``discount.discount_type`` / ``price_list.price_type``:
    Bounded to exactly ``IN_STOCK`` / ``SOLD`` / ``RETURNED`` / ``DAMAGED``,
    spelled out directly in the ERD's ``Important fields:`` line (no
    separate ``PART A`` enum entry exists for this vocabulary — it is
    written inline in the M3 line itself, unlike ``LotStatus``/
    ``CostingMethod`` which each have their own dedicated ``PART A`` block).
    ``state_token_type()`` (``VARCHAR(16)``) fits every member — ``RETURNED``
    and ``IN_STOCK`` are the longest at 8 characters each, well inside the
    16-char width, the same "does the longest member fit the factory width"
    check already performed for every other CHECK-bounded enum column in
    this codebase. A ``CheckConstraint`` via ``ck_index_name`` bounds the
    column to these four literal values, exactly the same pattern
    ``discount.discount_type`` / ``price_list.price_type`` /
    ``costing_method_config.method`` already use for their own explicit ERD
    enums. Declared ``NOT NULL`` with no default: the ERD gives no default
    value to transcribe, and a freshly-serialized unit's status is a real
    business fact the caller must supply (typically ``IN_STOCK`` at
    creation time, but that is an application-layer convention, not a
    schema-level default with textual basis in the ERD).

Uniqueness — column-level, NOT composite:
    The ERD's ``Unique: serial_number`` line names a single bare column, not
    a tuple — exactly the same shape ``system_config.key`` already
    established (a surrogate ``id`` PK plus one independently-unique
    business column). ``serial_number`` therefore uses column-level
    ``unique=True``, which the shared metadata naming convention renders as
    an ordinary ``uq_product_serial_serial_number`` unique constraint — not
    a composite constraint paired with ``product_id`` or any other column
    (contrast ``product_lot.lot_code``, whose ERD line explicitly reads
    "unique *per product*" and is therefore a composite
    ``UniqueConstraint(product_id, lot_code)`` instead — a deliberately
    different shape from this table's own bare, unqualified
    ``Unique: serial_number`` line).

No CHECK beyond the ``status`` vocabulary:
    ``product_id``/``lot_id`` are plain FKs with no additional business rule
    stated in the ERD line itself (unlike, say, a cross-column numeric-range
    CHECK elsewhere in this codebase); ``serial_number`` is free text with no
    further shape given. So only the one vocabulary CHECK is declared.

Soft delete — deliberately absent, per direct instruction:
    Unlike ``product.py`` (M1, "M + soft-deletable") / ``warehouse.py`` (M4,
    "M + soft-deletable") / ``warehouse_location.py`` (M5,
    "M + soft-deletable"), the ERD classifies ``product_serial`` as plain
    ``M`` — no "+ soft-deletable" qualifier. No ``deleted_at`` column is
    declared. Per direct instruction, this table's entire lifecycle is
    expressed through the ``status`` enum instead: a unit moving through
    ``IN_STOCK`` -> ``SOLD`` / ``RETURNED`` / ``DAMAGED`` *is* this table's
    lifecycle model, the same "status/window instead of soft-delete" split
    already established for ``product_lot`` (via ``LotStatus``, moving into
    ``EXPIRED``/``QUARANTINE``/``DAMAGED``) and, more distantly,
    ``price_list.is_active`` / ``discount.valid_to`` (a boolean flag / an
    open-ended time window rather than an enum, but the same underlying
    principle: lifecycle state lives in a business column, not in a
    ``deleted_at`` timestamp). A ``DAMAGED`` or ``RETURNED`` serial row is
    not hard-deleted or soft-deleted — it remains a live, queryable row
    whose ``status`` records what happened to that physical unit, which a
    ``deleted_at`` column would obscure rather than express.

Audit-column family — ``UniversalAuditColumns`` (UAC), per instruction:
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``ProductSerial`` uses UAC and opts its ``version`` column
    into SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), matching ``Product`` / ``ProductLot`` and every other
    UAC-using model in this codebase.

Naming convention:
    Both FKs use ``fk_index_name`` normally —
    ``fk_product_serial_product_id_product_id`` /
    ``fk_product_serial_lot_id_product_lot_id``. ``serial_number`` uses
    column-level ``unique=True`` -> ``uq_product_serial_serial_number`` (see
    dedicated "Uniqueness" section above — NOT composite). The ``status``
    CHECK uses ``ck_index_name`` -> bare descriptor ``status_values``,
    rendering ``ck_product_serial_status_values`` at compile time.

Column-type choices:

* ``serial_number`` — ``business_key_type()`` -> ``VARCHAR(40)``,
  column-level ``unique=True``.
* ``status`` — ``state_token_type()`` -> ``VARCHAR(16)``, CHECK-bounded to
  ``IN_STOCK`` / ``SOLD`` / ``RETURNED`` / ``DAMAGED``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import business_key_type, state_token_type


class ProductSerial(Base, UniversalAuditColumns):
    """``M3 — product_serial`` — unit-level serial tracking, optional per product (Classification: M)."""

    __tablename__ = "product_serial"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("product_serial", "product_id", "product"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------- lot_id
    # Nullable -- per direct ERD annotation "(nullable)" on this FK line.
    # See module docstring.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name("product_serial", "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    # --------------------------------------------------------- serial_number
    # Column-level unique=True -- independently unique, NOT composite
    # (contrast product_lot.lot_code's "unique per product" composite
    # shape). See module docstring's dedicated "Uniqueness" section.
    serial_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------------- status
    # Explicit ERD vocabulary, CHECK-bounded. Also this table's entire
    # lifecycle mechanism -- no deleted_at column (see module docstring).
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('IN_STOCK', 'SOLD', 'RETURNED', 'DAMAGED')",
            name=ck_index_name("product_serial", "status_values"),
        ),
    )


__all__ = ["ProductSerial"]
