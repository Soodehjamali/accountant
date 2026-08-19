"""``H9 (ERD id) — report_snapshot`` ORM model (immutable point-in-time capture of a report_run's output).

Authority: ``07_DATABASE_SPEC.md`` §H9 (spec's own section header:
``H9 (ERD id) — report_snapshot``; same "no dedicated ERD numeric id"
convention as ``notification_history`` (H8, this same change)) -- this
table **does** have a full detailed spec section, so the spec is primary
authority here; ``06_ERD.md`` (F.16 — Reporting Snapshots) is
secondary/corroborating only::

    H9 (ERD id) — report_snapshot
    Purpose: Immutable point-in-time capture of a report_run's structured
        output, for historical trend comparison across runs.
    PK: id (UUID)
    FK: report_run_id -> report_run.id; report_definition_id ->
        report_definition.id
    Column Definitions: +AAC; report_run_id UUID NOT NULL (FK, 1:1);
        report_definition_id UUID NOT NULL; snapshot_data JSONB NOT NULL
        (structured report output); captured_at TIMESTAMPTZ NOT NULL
        DEFAULT now(); row_count BIGINT NOT NULL DEFAULT 0
    Unique Constraints: uq_report_snapshot_run (report_run_id)
    Check Constraints: ck_report_snapshot_row_count_nonneg (row_count >= 0)
    Business Constraints: Append-only; a re-run produces a new report_run +
        new report_snapshot, never an update to a prior one.
    Recommended Indexes: btree on report_definition_id
    Composite Indexes: (report_definition_id, captured_at DESC) —
        trend-comparison query
    Partial Indexes: none
    Partitioning Strategy: Range partition by captured_at, with aggressive
        retention/rollup per ERD PART L (daily kept 90 days, then rolled up
        to weekly).
    Soft Delete Strategy: None
    Audit Strategy: created_by (AAC)
    Notes: GIN index on snapshot_data recommended only if ad-hoc querying
        into historical snapshot JSON is a real requirement — otherwise
        skip per JSONB Usage Policy (avoid indexing JSONB you don't query
        into).

1:1 child of ``report_run`` (T26, this same change) via ``report_run_id``;
also references ``report_definition`` (M17, already present in this
codebase) directly for trend-comparison queries across many runs of the
same saved report, without needing to join through ``report_run`` for that
dimension.

Non-reserved-word FK targets -- ``report_run_id -> report_run.id`` /
``report_definition_id -> report_definition.id``:
    Both are ordinary identifiers, no quoting concerns for either FK.

CRITICAL naming trap -- ``report_run_id``'s 1:1 unique constraint:
    The spec's literal constraint name is ``uq_report_snapshot_run`` --
    **not** ``uq_report_snapshot_report_run_id``, which is what
    column-level ``unique=True`` on ``report_run_id`` would produce via
    ``NAMING_CONVENTION["uq"]`` (``uq_%(table_name)s_%(column_0_name)s``).
    This model instead uses an **explicit**
    ``UniqueConstraint("report_run_id", name=uq_index_name(
    "report_snapshot", "run"))``, passing the helper a bare descriptor of
    ``"run"`` (not ``"report_run_id"``) so ``uq_index_name`` assembles
    ``uq_`` + ``report_snapshot`` + ``run`` -> ``uq_report_snapshot_run``
    exactly. The same "supply a short descriptor to the normal helper"
    treatment ``order_price_freeze.py``'s own
    ``uq_order_price_freeze_line`` 1:1-unique naming trap already
    established (NOT the bare-literal-string override treatment
    ``shipment_line.py`` / ``payment_allocation.py`` use for their own,
    differently-shaped naming traps).

``snapshot_data`` -- ``JSONB``, second JSONB column in this codebase to
follow ``report_definition.parameters``'s own precedent, typed
``Mapped[dict]`` for direct consistency with that sibling FK-target file:
    Declared via ``sqlalchemy.dialects.postgresql.JSONB`` directly, the
    same "consume the concrete dialect type directly, no
    ``database/types.py`` factory exists for JSON" treatment
    ``report_definition.parameters`` (already in this codebase) and
    ``order_price_freeze.precedence_chain_json`` both already establish.
    Typed as plain ``Mapped[dict]`` -- matching ``report_definition.py``'s
    own ``parameters: Mapped[dict]`` annotation exactly, rather than the
    more specific ``list[dict[str, Any]]`` annotation
    ``order_price_freeze.py`` uses for its own JSONB column. The two
    differ because the underlying JSON *shapes* differ: ``order_price_freeze
    .precedence_chain_json``'s own spec description names a JSON *array*
    ("ordered list of candidate price sources") explicitly, whereas this
    column's spec description -- "structured report output" -- names no
    specific top-level shape, and a report engine's structured output
    (columns/rows/metadata together) is conventionally wrapped in a single
    top-level JSON object, the same implicit shape
    ``report_definition.parameters`` itself already assumes for its own
    "parameter set" JSONB column. ``NOT NULL`` per spec, no default (every
    snapshot is written with real captured content at insert time).

``row_count`` -- ``BIGINT``, plain ``sqlalchemy.BigInteger``, ``NOT NULL
DEFAULT 0``:
    No ``database/types.py`` factory exists for ``BIGINT`` -- the same
    treatment ``report_run.row_count`` (this same change) receives, except
    this column is spec'd ``NOT NULL DEFAULT 0`` (a snapshot always
    captures a definite row count at write time, unlike ``report_run
    .row_count``'s own nullable-until-terminal-state treatment) via the
    dual ``default=0``/``server_default=sa_text("0")`` pattern this
    codebase's other defaulted numeric columns already use.

``captured_at`` -- ``NOT NULL DEFAULT now()``:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment every other AAC-adjacent
    posting-timestamp column in this codebase receives.

No separate business-actor column -- unlike ``notification_history
.actor_user_id`` / ``transfer_history.actor_user_id``:
    This table's own §3 Foreign Keys bullet list names only
    ``report_run_id`` and ``report_definition_id`` -- no third,
    business-actor FK to ``app_user.id`` is spec'd for this table (contrast
    ``notification_history``, this same change, whose own §3 line
    explicitly adds ``actor_user_id -> app_user.id``). Spec §13's own
    Audit Strategy line -- *"created_by (AAC)"* -- confirms AAC's own mixin
    ``created_by`` is this table's entire actor story (whichever
    job/service produced the snapshot), the same "no separate business
    actor column" shape ``order_price_freeze.py`` already has (spec §13:
    *"created_by (AAC)"* there too, with no distinct business-actor
    column of its own either).

Naming convention:
    ``report_run_id``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("report_snapshot", "run")``, NOT column-level
    ``unique=True``. The CHECK uses ``ck_index_name`` normally: the
    standard helper output already matches the spec's literal name
    verbatim (``ck_report_snapshot_row_count_nonneg``) -- no override
    needed. Both FKs use ``fk_index_name`` normally. The recommended
    single-column index uses ``idx_index_name`` with no override needed.

Composite index with a ``DESC`` column -- ``(report_definition_id,
captured_at DESC)``:
    Same ``sa_text("captured_at DESC")`` escape-hatch treatment
    ``report_run.py``'s own ``(report_definition_id, started_at DESC)``
    composite index receives -- see that module's docstring for the full
    rationale (``Index()`` has no plain-string spelling for "descending").

No ``deleted_at`` -- explicit per spec:
    Spec §12: *"None"* -- consistent with its append-only nature and the
    unconditional-absence treatment ``payment.py`` /
    ``notification_history.py`` (this same change) already receive for
    their own append-only Soft Delete Strategy notes.

No ``__mapper_args__ = {"version_id_col": ...}``:
    AAC carries no ``version`` column, so there is no optimistic-lock
    token to opt into here.

Out of scope for this model (not implemented here):
    * The GIN index on ``snapshot_data`` -- spec §15 explicitly makes this
      conditional ("recommended only if ad-hoc querying into historical
      snapshot JSON is a real requirement — otherwise skip"), i.e. a
      deliberately-not-built default per the spec's own stated JSONB Usage
      Policy, not an oversight.
    * Range partitioning by ``captured_at`` with retention/rollup -- spec
      marks this a physical-design/migration-time decision.
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    The spec's own §4 Column Definitions table opens with ``+AAC``, and §7
    Business Constraints states plainly *"Append-only; a re-run produces a
    new report_run + new report_snapshot, never an update to a prior
    one"* -- an unambiguous, spec-declared append-only table.
    ``ReportSnapshot`` therefore gets ``created_at`` / ``created_by`` only.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name, uq_index_name


class ReportSnapshot(Base, AppendOnlyAuditColumns):
    """``H9 (ERD id) — report_snapshot`` — immutable point-in-time capture of a report_run's output (Classification: H)."""

    __tablename__ = "report_snapshot"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- report_run_id
    # Unique via an explicit UniqueConstraint below (1:1 with report_run)
    # -- NOT column-level unique=True. See the module docstring's
    # "CRITICAL naming trap" note.
    report_run_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "report_run.id",
            name=fk_index_name("report_snapshot", "report_run_id", "report_run"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------- report_definition_id
    report_definition_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "report_definition.id",
            name=fk_index_name("report_snapshot", "report_definition_id", "report_definition"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- snapshot_data
    # Raw postgresql.JSONB, Mapped[dict] -- matches report_definition
    # .parameters's own typing exactly. See module docstring's dedicated
    # section.
    snapshot_data: Mapped[dict] = mapped_column(
        JSONB(),
        nullable=False,
    )

    # -------------------------------------------------------------- captured_at
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # --------------------------------------------------------------- row_count
    # Plain BigInteger -- no database/types.py factory exists for BIGINT.
    row_count: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "run" (not "report_run_id") so the assembled name
        # is uq_report_snapshot_run, not the longer
        # uq_report_snapshot_report_run_id that column-level unique=True's
        # implicit convention would produce.
        UniqueConstraint(
            "report_run_id",
            name=uq_index_name("report_snapshot", "run"),
        ),
        # CHECK: row_count non-negative.
        CheckConstraint(
            "row_count >= 0",
            name=ck_index_name("report_snapshot", "row_count_nonneg"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("report_snapshot", "report_definition_id"),
            "report_definition_id",
        ),
        # Composite index -- (report_definition_id, captured_at DESC). See
        # module docstring's dedicated section on the DESC column
        # expression (same treatment as report_run.py's own composite
        # index).
        Index(
            idx_index_name(
                "report_snapshot",
                composite_descriptor(("report_definition_id", "captured_at")),
            ),
            "report_definition_id",
            sa_text("captured_at DESC"),
        ),
    )


__all__ = ["ReportSnapshot"]
