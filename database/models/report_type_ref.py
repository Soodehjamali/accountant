"""``R10 — report_type_ref`` ORM model (report-kind registry).

Authority: docs/06_ERD.md, PART B → ``R10 — report_type_ref``::

    R10 — report_type_ref
    Purpose: Registry of report kinds (rep profit, product profit,
             KPI dashboards).
    PK: id | code unique
    Classification: R

This entity has **no entry in docs/07_DATABASE_SPEC.md** — PART B reference
tables were not ported into the physical spec; the ERD is the source of truth
for ``report_type_ref``'s own columns (``report_type_id`` appears as an FK
target on the ``report_definition`` table — M17 — in the spec).

Field surface (deliberately minimal):
    Exactly like R9 (``notification_type_ref``), the ERD's R10 entry lists
    **no "Important fields"** line — its only concrete fields are ``id`` and
    ``code`` (unique). Compare the sibling reference tables that *do* enumerate
    extra fields: R2 (``code``/``name``/``class``), R4
    (``code``/``sign``/``label``), R11 (``code``/``scope``/``label``). R10 lists
    none beyond the PK and the unique ``code``, so the model declares **only**
    ``id`` and ``code`` — no ``name`` / ``label`` / ``description`` /
    ``category`` is invented (the ERD is the source of truth and lists none).
    The ERD's "Purpose" prose ("Registry of report kinds (rep profit, product
    profit, KPI dashboards)") names the *kinds of report* the catalog will list,
    not additional columns on the catalog table. If a future spec revision adds
    a human-readable label / schedule-default / parameters-template column to
    R10, it is added then; not preemptively.

Relationship to bounded value sets elsewhere:
    No PART A enum named ``ReportType`` (or any report-related enum) exists —
    the PART A enum list has no report-type bounded set. The bounded value sets
    adjacent to reporting live on the **consumer** tables, not on R10:
    ``report_definition`` (M17) carries ``output_format`` / ``schedule_cron``,
    and ``report_run`` (T26) carries ``status (QUEUED/RUNNING/COMPLETE/FAILED)``
    — that bounded set is enforced on T26, not on this reference catalog.
    R10's own field list declares no bounded-value column, so — following the
    same discipline as ``notification_type_ref`` and ``city_ref`` (which added
    no CHECK because the ERD implied no bounded set for their R-table fields) —
    **no CHECK constraint is added here**.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` / ``version``.
    The ERD's §0.2 Governing Design Decisions states audit fields are stored by
    *every* table, so R-class editable reference tables adopt UAC. A report-type
    catalog is runtime-editable (data teams add/retire report kinds as new
    analytical requirements land), so it gets UAC. Like ``currency`` /
    ``reason_code_ref`` / ``movement_type_ref`` / ``city_ref`` /
    ``unit_of_measure`` / ``notification_type_ref``, it deliberately carries
    **no** ``deleted_at`` / soft-delete: the ERD does not list a soft-delete
    column for R10, and a reference catalog is retired by discontinuing use,
    not soft-deleted (``UniversalAuditColumns`` as defined in
    ``database.mixins`` already carries no ``deleted_at``, so the mixin is used
    as-is).

Optimistic locking:
    ``__mapper_args__ = {"version_id_col": "version"}`` opts the model into the
    UAC ``version`` column as the SQLAlchemy row-version concurrency token (per
    ``database.mixins``'s documented opt-in mechanism — the mixin supplies the
    column, the model wires the mapper). Same pattern as the other R-class
    reference models.

Naming convention:
    The column-level ``unique=True`` on ``code`` auto-named via the shared
    ``MetaData`` naming convention to ``uq_report_type_ref_code``
    (``uq_%(table_name)s_%(column_0_name)s``). There is **no** CHECK constraint
    (see "Relationship to bounded value sets elsewhere" above — R10 implies no
    bounded set), so no ``ck_*`` name is generated. No explicit operational index
    is authored for this minimal catalog.

Column-type choices (prefer existing ``database.types`` helpers over raw
``String(N)`` literals — no length invented):

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``. A report-type code is a
  short controlled-vocabulary token naming a report kind in the ERD's own
  examples ("rep profit, product profit, KPI dashboards"). Measured realistic
  longest values: ``REP_PROFIT_ANALYSIS`` = 19, ``PRODUCT_PROFIT_MARGIN`` = 21,
  ``KPI_DASHBOARD_SUMMARY`` = 21, ``REPRESENTATIVE_COMMISSION_REPORT`` = 31,
  ``CUSTOMER_RETURN_AGING_REPORT`` = 27 — the worst realistic case is ~31 chars.
  The helper's documented purpose is "SKU / warehouse code / currency ISO-3 /
  short codes"; ``StringLength.CODE_SHORT`` (40) is the existing, non-invented,
  authoritative fit — the 40-char budget holds every realistic report-kind code
  (longest ~31) with real headroom and without inventing a new ``StringLength``
  member. Same choice as ``reason_code_ref.code`` / ``movement_type_ref.code`` /
  ``city_ref.code`` / ``unit_of_measure.code`` / ``notification_type_ref.code``.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import code_short_type


class ReportTypeRef(Base, UniversalAuditColumns):
    """``R10 — report_type_ref`` — report-kind registry (Classification: R)."""

    __tablename__ = "report_type_ref"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token (mixins.py opt-in mechanism).
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- code
    # Short controlled-vocabulary code naming a report kind (e.g.
    # REP_PROFIT_ANALYSIS, PRODUCT_PROFIT_MARGIN, KPI_DASHBOARD_SUMMARY).
    # ``code_short_type`` (VARCHAR(40)) is the existing, non-invented helper
    # whose documented purpose is short codes; the 40-char budget holds every
    # realistic report-kind code (longest measured ~31 chars) with headroom
    # and without inventing a new ``StringLength`` member.
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # No CHECK constraints: R10's own ERD field list declares no bounded-value
    # column, and no PART A ``ReportType`` enum exists. The bounded sets
    # adjacent to reporting (report_run.status) live on the consumer table T26,
    # not here — see the module docstring. None invented (same discipline as
    # ``notification_type_ref`` / ``city_ref``).

    # No ``__table_args__``: no CHECK constraints, no explicit operational
    # indexes for this minimal catalog. The sole unique constraint is the
    # column-level ``unique=True`` on ``code`` (auto-named). Declaring
    # ``__table_args__`` would imply constraints that the ERD does not.


__all__ = ["ReportTypeRef"]
