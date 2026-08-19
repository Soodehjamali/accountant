"""``C5 — warehouse_assignment`` ORM model (representative ↔ warehouse, with primary flag).

Authority: ``06_ERD.md``, line 40 → ``C5 — warehouse_assignment``::

    C5 — warehouse_assignment
    Purpose: Assign representative ↔ warehouse (1:N) with primary flag.
    PK: id
    FK: representative_id → representative, warehouse_id → warehouse
    Important fields: is_primary (bool), effective_from, effective_to
    Unique: (representative_id, warehouse_id); conditional uniqueness on
        (representative_id, is_primary=true)
    Classification: C

Same gap as every other table with no dedicated spec section so far
(``commission_config.py`` (C1), ``discount.py`` (H3), ``price_list.py``
(C3), etc.): ``06_ERD.md`` is ``warehouse_assignment``'s sole authority --
``warehouse_assignment`` has no detailed section in
``07_DATABASE_SPEC.md`` (confirmed by search: the only
``07_DATABASE_SPEC.md``-adjacent mention is the ERD's own cross-reference
line, *"representative N—N warehouse via warehouse_assignment (C5)"** --
no §-numbered ``warehouse_assignment`` table section of its own).

Both FKs are real from the outset:
    ``representative`` and ``warehouse`` both already exist in this
    codebase, so ``representative_id`` and ``warehouse_id`` are declared as
    real ``ForeignKey()`` constraints from the start. Neither is marked
    nullable in the ERD's ``FK:`` line, so both are declared ``NOT NULL`` --
    an assignment row with no representative or no warehouse is
    meaningless.

``is_primary`` -- boolean flag, default ``false``:
    A plain ``Boolean``, ``NOT NULL``, defaulting to ``false`` -- the same
    reasoning already established for ``representative_contact.is_primary``
    / ``customer_contact.is_primary``: "primary" is an explicit
    designation among a representative's several warehouse assignments,
    not the default state of every newly-created assignment row.

``effective_from`` / ``effective_to`` -- time-bounded validity:
    Same pattern as ``commission_config.effective_from`` /
    ``effective_to`` and ``discount.valid_from`` / ``valid_to``:
    ``effective_from`` is ``NOT NULL`` (every assignment has a start of
    validity); ``effective_to`` is nullable, representing open-ended
    validity (an assignment with no scheduled end date) -- the ERD gives
    no explicit nullable annotation for either column (same terse style as
    those two tables' own ERD lines), so this follows the identical
    precedent already established there.

Two distinct uniqueness rules -- an ordinary composite ``UniqueConstraint``
PLUS a conditional partial unique index, exactly as the ERD specifies both:
    The ERD's ``Unique:`` line gives **two** separate uniqueness rules for
    this table, not one, and both are implemented:

    1. ``(representative_id, warehouse_id)`` -- an ordinary composite
       ``UniqueConstraint`` via ``uq_index_name`` + ``composite_descriptor``
       (a representative cannot be assigned to the same warehouse twice).
       Literal ERD column list, ordinary composite case -- the same
       treatment ``commission_config`` / ``price_history`` /
       ``warehouse_location`` / the contact tables' own literal composite
       uniqueness already received.
    2. ``"conditional uniqueness on (representative_id, is_primary=true)"``
       -- *"at most one primary warehouse per representative"* -- this is
       NOT expressible as an ordinary ``UniqueConstraint`` (which has no
       ``WHERE`` clause), so it is implemented as a unique **partial**
       index, mirroring ``warehouse.py``'s own
       ``idx_warehouse_one_active_factory`` pattern *exactly* as
       instructed: ``Index(..., "representative_id", unique=True,
       postgresql_where=sa_text("is_primary = true"))``. The mechanism is
       the same idiom ``warehouse.py``'s own docstring documents for its
       "exactly one active FACTORY" rule (also mirrored from
       ``currency.py``'s ``idx_currency_one_base``): the index is
       ``UNIQUE`` on the *discriminating* column
       (``representative_id`` here, ``type`` there) but the
       ``postgresql_where`` predicate (``is_primary = true`` here,
       ``type = 'FACTORY' AND status = 'ACTIVE'`` there) restricts which
       rows the index even considers -- so "unique within the filtered
       subset" becomes "at most one row per representative_id where
       is_primary is true", exactly the ERD's stated conditional rule, with
       no application-level enforcement needed. This is the second
       instance of this idiom in the codebase (after ``currency`` /
       ``warehouse``), applied here on direct instruction to mirror it.

No CHECK constraints -- the ERD names no vocabulary/enum field on this
table (``is_primary`` is a plain boolean, ``effective_from``/
``effective_to`` are plain timestamps), so there is no vocabulary CHECK to
write here, unlike ``discount``/``price_list``/``order_status_history``/
``warehouse`` itself.

Soft delete -- deliberately absent, same reasoning as
``commission_config``/``discount``/``price_list``:
    The ERD classifies ``warehouse_assignment`` as plain ``C`` with no
    "+ soft-deletable" qualifier. No ``deleted_at`` column is declared; the
    ``effective_from``/``effective_to`` validity window already expresses
    this assignment's own lifecycle, the same role it plays on
    ``commission_config`` / ``discount``.

Audit-column family -- ``UniversalAuditColumns`` (UAC), per instruction:
    Plain ``C`` classification, ordinary mutable business record -- the
    same reasoning already established for ``commission_config`` (C1) and
    ``price_list`` (C3), both plain ``C``, both UAC.
    ``WarehouseAssignment`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), consistent with every other UAC-using model in this
    codebase.

Naming convention:
    Both FKs use ``fk_index_name`` normally
    (``fk_warehouse_assignment_representative_id_representative_id``,
    ``fk_warehouse_assignment_warehouse_id_warehouse_id``). The ordinary
    composite unique constraint uses ``uq_index_name`` +
    ``composite_descriptor`` ->
    ``uq_warehouse_assignment_representative_id_warehouse_id``. The
    conditional partial unique index uses
    ``idx_index_name("warehouse_assignment", "one_primary_warehouse")`` ->
    ``idx_warehouse_assignment_one_primary_warehouse``, mirroring
    ``warehouse.idx_warehouse_one_active_factory`` -- see the dedicated
    section above.

Column-type choices:

* ``is_primary`` -- ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT false``.
* ``effective_from`` / ``effective_to`` -- ``DateTime(timezone=True)``.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import composite_descriptor, fk_index_name, idx_index_name, uq_index_name


class WarehouseAssignment(Base, UniversalAuditColumns):
    """``C5 — warehouse_assignment`` — assign representative ↔ warehouse (1:N) with primary flag (Classification: C)."""

    __tablename__ = "warehouse_assignment"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------- representative_id
    representative_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("warehouse_assignment", "representative_id", "representative"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- warehouse_id
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("warehouse_assignment", "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------ is_primary
    is_primary: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    # -------------------------------------------------------------- effective_from
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ---------------------------------------------------------------- effective_to
    # Nullable -- open-ended validity, same precedent as
    # commission_config.effective_to / discount.valid_to.
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE #1 -- ordinary composite case, literal ERD column pair.
        UniqueConstraint(
            "representative_id",
            "warehouse_id",
            name=uq_index_name(
                "warehouse_assignment",
                composite_descriptor(("representative_id", "warehouse_id")),
            ),
        ),
        # UNIQUE #2 -- conditional partial unique index, "at most one
        # primary warehouse per representative". Mirrors
        # warehouse.idx_warehouse_one_active_factory exactly -- see module
        # docstring's dedicated section.
        Index(
            idx_index_name("warehouse_assignment", "one_primary_warehouse"),
            "representative_id",
            unique=True,
            postgresql_where=sa_text("is_primary = true"),
        ),
    )


__all__ = ["WarehouseAssignment"]
