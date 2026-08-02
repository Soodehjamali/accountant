"""``M6 — representative`` ORM model (sales representative master).

Authority: ``06_ERD.md``, PART C → ``M6 — representative``::

    M6 — representative
    Purpose: Sales representative master (SRS E12).
    PK: id
    FK: home_city_ref_id → city_ref
    Important fields: person_name, code (unique), national_id/tax_id,
                      status (ACTIVE/SUSPENDED/OFFBOARDED),
                      commission_config_id → commission_config
    Unique: code
    Business constraints: offboarding requires stock transferred back;
                          status change audited
    Classification: M + soft-deletable

Same gap as every other M-table so far: ``06_ERD.md`` is M6's sole
authority — M6 has no detailed section in ``07_DATABASE_SPEC.md``.

``home_city_ref_id`` — nullable FK:
    ``home_city_ref_id → city_ref`` (R13). The ERD does not mark it
    required, so it is nullable — the exact same treatment
    ``warehouse.city_ref_id`` gives its own ``city_ref`` FK.

``commission_config_id`` — ``ForeignKey()`` retrofitted now that C1 exists:
    ``commission_config_id`` was originally a plain ``UUID`` column with no
    ``ForeignKey()`` because ``commission_config`` (C1) did not exist in the
    codebase yet — the same deferred-FK deviation used at the time for
    ``app_user.representative_id`` and ``inventory_transaction.lot_id``. Now
    that ``database/models/commission_config.py`` has landed, this column
    carries a real ``ForeignKey("commission_config.id", ...)``, named via
    ``fk_index_name`` →
    ``fk_representative_commission_config_id_commission_config_id`` (the
    trailing ``_id`` is ``fk_index_name``'s default
    ``referred_column_name="id"``, appended after the referred table name —
    verified below, not assumed from the table name alone). ``nullable=True``
    is unchanged: the ERD's "nullable for global default" reasoning (see
    ``commission_config.py``'s own docstring) still applies — a
    representative need not have a bespoke commission_config row bound to
    it; the column simply now enforces referential integrity for the rows
    that do populate it.

``national_id`` / ``tax_id`` — reading, stated explicitly:
    This is a different kind of ambiguity than ``app_user``'s
    ``username``/``email`` line. There, "Unique: username (or email)" reads
    as an either/or *alternation* over what can serve as one login
    identifier. Here, "national_id/tax_id" is read instead as **two separate
    sibling identifier fields** — a national ID and a tax ID are distinct
    real-world documents (a person's civil identity number vs. a tax-filing
    number), not two names for the same value, and the ERD's own ``Unique:``
    line only names ``code``, not either identifier — there is no
    alternation being expressed. This model therefore implements **two**
    columns, ``national_id`` and ``tax_id``, both nullable (the ERD does not
    mark either required) and with no uniqueness constraint on either (the
    ERD's ``Unique:`` line does not name them).

``status`` — explicit ERD vocabulary, NOT an assumption:
    Unlike ``app_user.status`` / ``warehouse.status`` / ``carrier.status``
    (all of which are placeholder ``ACTIVE``/``INACTIVE`` assumptions this
    codebase invented because PART A gives no dedicated vocabulary for
    them), ``representative.status`` is explicitly spelled out in the ERD's
    own M6 line: ``status (ACTIVE/SUSPENDED/OFFBOARDED)``. The CHECK below
    is transcribed directly from the ERD text, not assumed.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Representative`` uses UAC and opts its ``version`` column
    into SQLAlchemy optimistic locking (``__mapper_args__ =
    {"version_id_col": "version"}``), exactly like ``Currency`` / ``Product``
    / ``Carrier`` / ``Warehouse`` / ``AppUser``.

Soft delete:
    Classification is "M + soft-deletable" — same as ``product`` (M1),
    ``warehouse`` (M4), and ``app_user`` (M10). Following their precedent, no
    reusable soft-delete mixin is relied on; ``deleted_at`` is declared
    directly as a nullable timezone-aware ``TIMESTAMPTZ``, default ``NULL``
    meaning not deleted.

Business constraints — service-layer only, no SQL:
    "offboarding requires stock transferred back" is a cross-table rule (it
    depends on ``inventory_transaction`` / derived stock state at the
    representative's warehouse(s), not on this row alone) — the same
    treatment every other cross-table/temporal rule has received so far
    (e.g. ``warehouse.py``'s "cannot deactivate a warehouse holding
    non-zero stock"). "status change audited" is a service-layer /
    ``audit_log`` concern (recording who changed status and when), not a
    row-local constraint expressible as a CHECK. Neither is encoded as SQL
    here.

Naming convention:
    ``code`` uses column-level ``unique=True`` → ``uq_representative_code``,
    mirroring ``warehouse.code`` / ``product.sku``. ``home_city_ref_id`` uses
    ``fk_index_name`` → ``fk_representative_home_city_ref_id_city_ref_id``
    (the trailing ``_id`` is ``fk_index_name``'s default
    ``referred_column_name="id"``, appended after the referred table name),
    mirroring ``warehouse.city_ref_id``. The ``status`` vocabulary is bounded
    by a CHECK named via ``ck_index_name`` →
    ``ck_representative_status_values``.

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``.
* ``person_name`` — ``name_type()`` → ``VARCHAR(160)``.
* ``national_id`` / ``tax_id`` — ``code_short_type()`` → ``VARCHAR(40)``
  each, the same short controlled-identifier width used by ``code`` (see
  note above).
* ``status`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``ACTIVE`` / ``SUSPENDED`` / ``OFFBOARDED`` (explicit ERD vocabulary —
  see note above).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import code_short_type, name_type, state_token_type


class Representative(Base, UniversalAuditColumns):
    """``M6 — representative`` — sales representative master (Classification: M + soft-deletable)."""

    __tablename__ = "representative"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------ home_city_ref_id
    # ERD does not mark this required — nullable, mirrors
    # warehouse.city_ref_id.
    home_city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "city_ref.id",
            name=fk_index_name("representative", "home_city_ref_id", "city_ref"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- code
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------- person_name
    person_name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # ------------------------------------------------- national_id / tax_id
    # Two separate sibling identifier fields, not an either/or alternation —
    # see module docstring. Neither is marked required or unique by the ERD.
    national_id: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )
    tax_id: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )

    # -------------------------------------------------------------- status
    # Explicit ERD vocabulary (NOT an assumption — see module docstring):
    # ACTIVE / SUSPENDED / OFFBOARDED.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------- commission_config_id
    # Retrofitted to a real ForeignKey now that commission_config (C1)
    # exists (see module docstring). Nullable, unchanged.
    commission_config_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "commission_config.id",
            name=fk_index_name("representative", "commission_config_id", "commission_config"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker (same pattern as product.py /
    # warehouse.py / app_user.py); NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: status vocabulary — explicit ERD text, not an assumption
        # (see module docstring).
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'OFFBOARDED')",
            name=ck_index_name("representative", "status_values"),
        ),
    )


__all__ = ["Representative"]
