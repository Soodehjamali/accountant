"""``M4 — warehouse`` ORM model (factory + representative warehouses).

Authority: ``06_ERD.md``, PART C → ``M4 — warehouse``::

    M4 — warehouse
    Purpose: Factory + representative warehouses (SRS E6).
    PK: id
    Important fields: code (unique), name, type (WarehouseType),
                      ownership_mode (OwnershipMode), address,
                      city_ref_id → city_ref, latitude, longitude, status,
                      responsible_user_id → app_user
    Unique: code
    Business constraints: exactly ONE type=FACTORY active at a time
                          (DB-level conditional uniqueness); cannot
                          deactivate a warehouse holding non-zero stock;
                          ownership_mode immutable once stock exists
    Classification: M + soft-deletable

Same gap as ``product`` (M1): ``06_ERD.md`` is M4's sole authority — M4 has
no detailed section in ``07_DATABASE_SPEC.md``.

PART A enum vocabularies consumed here::

    WarehouseType    FACTORY, REPRESENTATIVE
    OwnershipMode    OWNED, CONSIGNMENT

``warehouse.status`` — ASSUMPTION, not a silent fact:
    ``status`` is listed as an "Important field" in M4 but is **not** one of
    the promoted/listed enums in PART A — no dedicated vocabulary is given
    for it anywhere in the ERD. This model assumes ``ACTIVE`` / ``INACTIVE``,
    mirroring ``carrier.py``'s ``status`` CHECK pattern (``R14 — carrier``
    has the identical ACTIVE/INACTIVE shape stated explicitly in its own ERD
    entry). This is an assumption made to fill an unspecified vocabulary, not
    a value taken from the ERD text itself.

Address — no dedicated type exists yet:
    The ERD lists a bare ``address`` field with no further shape. No
    address-specific column-type helper exists in ``database.types``.
    ``description_type()`` (``VARCHAR(255)``) is used as the closest existing
    factory rather than inventing a new one; flagged here as a placeholder,
    not a considered address representation (no structured street/unit/postal
    breakdown).

``responsible_user_id`` — no ``ForeignKey()`` yet:
    ``app_user`` (M10) does not exist yet, so ``responsible_user_id`` is a
    plain ``UUID`` column with **no** ``ForeignKey()`` — the exact same
    deviation already documented in ``database.mixins`` for
    ``created_by`` / ``updated_by`` (UAC/AAC). Nullable, since the ERD does
    not mark it required. The FK is added by a later migration once
    ``app_user`` lands.

``city_ref_id`` — nullable FK:
    ``city_ref_id → city_ref`` (R13). The ERD does not mark it required, so
    it is nullable, exactly like ``product.category_id`` in ``product.py``.

``latitude`` / ``longitude``:
    ``geo_type()`` → ``NUMERIC(9, 6)`` — the same factory
    ``shipment_status_history``'s ``lat`` / ``lng`` use, per
    ``database.types.geo_type()``'s own docstring. The same ``[-90, 90]`` /
    ``[-180, 180]`` range CHECKs that docstring describes are added here,
    named via ``ck_index_name``. The ERD does not mark these two fields
    required either (only ``code`` / ``name`` / ``type`` / ``ownership_mode``
    read as the entity's load-bearing identity fields), so — consistent with
    how ``address`` / ``city_ref_id`` / ``responsible_user_id`` are all
    treated as nullable above — ``latitude`` / ``longitude`` are nullable
    too. This is an assumption, stated explicitly: not every warehouse
    necessarily has geo-tracking coordinates captured.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Warehouse`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), exactly like ``Currency`` / ``Product`` / ``Carrier``.

Soft delete:
    Classification is "M + soft-deletable" — same as ``product`` (M1).
    Following ``product.py``'s precedent, no reusable soft-delete mixin is
    relied on; ``deleted_at`` is declared directly as a nullable
    timezone-aware ``TIMESTAMPTZ``, default ``NULL`` meaning not deleted.

Business constraints — service-layer only:
    "cannot deactivate a warehouse holding non-zero stock" and
    "ownership_mode immutable once stock exists" are both cross-table /
    temporal rules (they depend on ``inventory_transaction`` / derived stock
    state, not on this row alone) — the same treatment every other
    cross-table/temporal rule has received so far (e.g. ``carrier.py``'s
    "cannot deactivate a carrier with an IN_TRANSIT shipment"). They are
    documented here, not encoded as a SQL CHECK.

Naming convention:
    ``code`` uses column-level ``unique=True`` → ``uq_warehouse_code``.
    ``city_ref_id`` uses ``fk_index_name`` → ``fk_warehouse_city_ref_id_city_ref``.
    ``type`` / ``ownership_mode`` / ``status`` vocabularies are bounded by
    CHECKs named via ``ck_index_name``, mirroring ``carrier.py``'s
    ``ck_carrier_status_values`` pattern:
    ``ck_warehouse_type_values``, ``ck_warehouse_ownership_mode_values``,
    ``ck_warehouse_status_values``. The lat/lng range CHECKs are named
    ``ck_warehouse_latitude_range`` / ``ck_warehouse_longitude_range``,
    mirroring ``currency.py``'s ``ck_currency_decimals_range`` pattern.
    The "exactly one active FACTORY" partial unique index is named via
    ``idx_index_name("warehouse", "one_active_factory")`` →
    ``idx_warehouse_one_active_factory``, mirroring ``currency.py``'s
    ``idx_currency_one_base`` **exactly** (unique partial index on the
    discriminating column, filtered by a ``postgresql_where`` predicate).

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``.
* ``name`` — ``name_type()`` → ``VARCHAR(160)``.
* ``type`` / ``ownership_mode`` / ``status`` — ``state_token_type()`` →
  ``VARCHAR(16)`` each, bounded by their respective CHECKs.
* ``address`` — ``description_type()`` → ``VARCHAR(255)`` (see note above).
* ``latitude`` / ``longitude`` — ``geo_type()`` → ``NUMERIC(9, 6)``.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name
from database.types import code_short_type, description_type, geo_type, name_type, state_token_type


class Warehouse(Base, UniversalAuditColumns):
    """``M4 — warehouse`` — factory + representative warehouses (Classification: M + soft-deletable)."""

    __tablename__ = "warehouse"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- code
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------------- name
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- type
    # Bounded to WarehouseType (PART A): FACTORY, REPRESENTATIVE.
    type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ---------------------------------------------------- ownership_mode
    # Bounded to OwnershipMode (PART A): OWNED, CONSIGNMENT.
    ownership_mode: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ----------------------------------------------------------- address
    # No dedicated address type exists yet — description_type() is the
    # closest existing factory (see module docstring note above).
    address: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )

    # ------------------------------------------------------- city_ref_id
    city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "city_ref.id",
            name=fk_index_name("warehouse", "city_ref_id", "city_ref"),
        ),
        nullable=True,
    )

    # ----------------------------------------------------- latitude/longitude
    # Same factory as shipment_status_history's lat/lng (geo_type()'s own
    # docstring); nullable — the ERD does not mark these required (see
    # module docstring note above).
    latitude: Mapped[float | None] = mapped_column(
        geo_type(),
        nullable=True,
    )
    longitude: Mapped[float | None] = mapped_column(
        geo_type(),
        nullable=True,
    )

    # ------------------------------------------------------------- status
    # ASSUMPTION: no dedicated vocabulary is given in PART A for
    # warehouse.status. Mirrors carrier.py's status pattern: ACTIVE/INACTIVE.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------- responsible_user_id
    # Plain UUID column, NO ForeignKey() — app_user (M10) does not exist yet.
    # Same deviation already documented in database/mixins.py for
    # created_by/updated_by. Nullable — the ERD does not mark it required.
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        nullable=True,
    )

    # -------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker (same pattern as product.py);
    # NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: WarehouseType vocabulary (PART A).
        CheckConstraint(
            "type IN ('FACTORY', 'REPRESENTATIVE')",
            name=ck_index_name("warehouse", "type_values"),
        ),
        # CHECK: OwnershipMode vocabulary (PART A).
        CheckConstraint(
            "ownership_mode IN ('OWNED', 'CONSIGNMENT')",
            name=ck_index_name("warehouse", "ownership_mode_values"),
        ),
        # CHECK: status vocabulary — ASSUMPTION (see module docstring).
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name=ck_index_name("warehouse", "status_values"),
        ),
        # CHECK: latitude / longitude ranges, per geo_type()'s docstring.
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name=ck_index_name("warehouse", "latitude_range"),
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name=ck_index_name("warehouse", "longitude_range"),
        ),
        # Business constraint: exactly ONE type=FACTORY active at a time.
        # Mirrors currency.py's idx_currency_one_base EXACTLY — a unique
        # partial index on the discriminating column ("type"), filtered to
        # the rows the invariant actually governs. Because the
        # postgresql_where predicate already restricts matched rows to
        # type = 'FACTORY', a unique constraint on "type" among those rows
        # permits at most one.
        Index(
            idx_index_name("warehouse", "one_active_factory"),
            "type",
            unique=True,
            postgresql_where=sa_text("type = 'FACTORY' AND status = 'ACTIVE'"),
        ),
    )


__all__ = ["Warehouse"]
