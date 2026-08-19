"""``T26 — report_run`` ORM model (a generated report instance/output, linked to its stored document).

Authority: ``07_DATABASE_SPEC.md`` §T26 — ``T26 — report_run`` **has** a
full detailed section in the physical spec, so the spec is primary
authority here; ``06_ERD.md`` (F.12 — Reporting) is
secondary/corroborating only::

    T26 — report_run
    Purpose: A generated report instance/output, linked to its stored
        document (SRS E33).
    PK: id (UUID)
    FK: report_definition_id -> report_definition.id; generated_document_id
        -> generated_document.id (nullable); triggered_by -> app_user.id
        (nullable)
    Column Definitions: +UAC; report_definition_id UUID NOT NULL;
        generated_document_id UUID NULL; triggered_by UUID NULL (NULL =
        scheduler); status VARCHAR(16) NOT NULL DEFAULT 'QUEUED'; started_at
        TIMESTAMPTZ NULL; completed_at TIMESTAMPTZ NULL; row_count BIGINT
        NULL (rows in the resulting output)
    Unique Constraints: none beyond PK
    Check Constraints: ck_report_run_status (status IN
        ('QUEUED','RUNNING','COMPLETE','FAILED')); ck_report_run_row_count_nonneg
        (row_count IS NULL OR row_count >= 0)
    Business Constraints: none beyond referential integrity
    Recommended Indexes: btree on report_definition_id
    Composite Indexes: (report_definition_id, started_at DESC) — "latest
        runs for this definition"
    Partial Indexes: idx_report_run_active ON report_run (status) WHERE
        status IN ('QUEUED','RUNNING')
    Partitioning Strategy: Range partition by started_at (yearly) if report
        volume grows large
    Soft Delete Strategy: Supported
    Audit Strategy: Standard UAC
    Notes: —

Not owned by ``report_definition`` in the strict aggregate-child sense the
way ``shipment_line`` is owned by ``shipment`` -- ``report_run`` is a
T-classified transactional record of a single execution, referencing its
``report_definition`` (the saved configuration it was run from, already
present in this codebase per M17) and optionally its resulting
``generated_document`` (already present per M16) once the run completes.
``report_snapshot`` (H9, this same change) in turn carries a 1:1
``report_run_id`` FK back to this table.

Audit-mixin decision -- ``UniversalAuditColumns`` (UAC), per the spec's own
explicit ``+UAC`` marker, NOT append-only despite sitting alongside two
AAC-classified siblings in this same change:
    Unlike ``notification_history`` (H8) and ``report_snapshot`` (H9, this
    same change) -- both spec'd ``+AAC`` -- this table's own §4 Column
    Definitions table opens with ``+UAC``, and its §2 classification is
    ``T26`` (a ``T``-prefixed, ordinary transactional table), not one of
    the ``H``-prefixed append-only history tables. This makes sense
    structurally: a single report-run row's own ``status`` genuinely
    mutates in place over its lifecycle (``QUEUED`` -> ``RUNNING`` ->
    ``COMPLETE``/``FAILED``, with ``started_at``/``completed_at``/
    ``row_count`` back-filled as the run progresses) -- the same
    "long-lived row whose own fields are updated in place" shape
    ``notification`` (T24) itself has, which is also UAC despite standing
    immediately upstream of its own AAC-classified history table
    (``notification_history``, this same change). ``ReportRun`` therefore
    gets the full ``created_at``/``updated_at``/``created_by``/
    ``updated_by``/``version`` set and opts its ``version`` column into
    optimistic locking via ``__mapper_args__ = {"version_id_col":
    "version"}``, the same treatment ``notification.py`` /
    ``report_definition.py`` (both already in this codebase) already give
    their own UAC-classified tables.

Non-reserved-word FK targets -- ``report_definition_id ->
report_definition.id`` / ``generated_document_id -> generated_document.id``
/ ``triggered_by -> app_user.id``:
    All three are ordinary identifiers, no quoting concerns for any FK.

``generated_document_id`` -- nullable, populated only once the run
completes and produces a stored document:
    A ``QUEUED``/``RUNNING``/``FAILED`` run has no document yet (or ever,
    in the ``FAILED`` case) -- the spec gives this column no ``NOT NULL``,
    consistent with the report-generation lifecycle described in the
    Purpose line ("a generated report instance/output, **linked to** its
    stored document" -- the link is established once output exists, not at
    row creation).

``triggered_by`` -- nullable, distinct from UAC's own ``created_by``:
    This table's own spec'd business actor ("who kicked off this specific
    run"), nullable because a scheduled run has no human trigger (spec:
    "NULL = scheduler"). The same "business column vs. mixin audit column,
    same target table, different semantic role" situation
    ``shipment.shipped_by`` / ``stock_transfer.requested_by`` already
    document -- here paired against UAC's own ``created_by`` (which, for a
    scheduler-triggered run, would typically be a system/service account
    rather than NULL, since UAC's own ``created_by`` is ``NOT NULL`` by
    contract).

``status`` -- ``VARCHAR(16)``, EXACT spec match via ``state_token_type()``:
    Spec'd exactly ``VARCHAR(16)`` -- precisely ``state_token_type()``'s own
    width, no placeholder needed (the same exact-match situation
    ``shipment.state`` / ``shipment.shipping_payer`` already enjoy).
    ``NOT NULL DEFAULT 'QUEUED'`` mirrors ``notification.state``'s own
    dual ``default=``/``server_default=sa_text(...)`` quoted-string-default
    pattern, and reuses the exact same ``'QUEUED'`` starting value
    ``notification.state`` itself defaults to.

``row_count`` -- ``BIGINT``, plain ``sqlalchemy.BigInteger``, nullable:
    No ``database/types.py`` factory exists for ``BIGINT`` (its scope is
    limited to ``NumericPrecision``/``StringLength`` members) -- the same
    "consume the concrete SQLAlchemy type directly when no factory
    abstraction yet exists for it" treatment ``notification_history.py``'s
    own ``retry_attempt`` (``SmallInteger``) receives one level down the
    integer-width scale. Nullable per spec: unknown/not-yet-computed until
    the run reaches a terminal state.

``started_at`` / ``completed_at`` -- nullable, no default, back-filled by
the application as the run progresses:
    ``DateTime(timezone=True)``, matching every other lifecycle-timestamp
    column in this codebase (``shipment.shipped_at``/``delivered_at``,
    ``stock_transfer.approved_at``/``dispatched_at``/``received_at``).

Soft-delete -- ``deleted_at``, per spec:
    Spec §12: *"Supported"* -- unqualified, unlike ``shipment``'s /
    ``invoice``'s own conditional soft-delete notes. ``deleted_at`` is
    added, unconditionally nullable.

Naming convention:
    Both CHECKs use ``ck_index_name`` normally: the standard helper output
    already matches the spec's two literal names verbatim
    (``ck_report_run_status``, ``ck_report_run_row_count_nonneg``) -- no
    override needed. All three FKs use ``fk_index_name`` normally. The
    recommended single-column index uses ``idx_index_name`` with no
    override needed. The partial index ``idx_report_run_active`` is
    produced by plain ``idx_index_name("report_run", "active")`` -- the
    helper's normal output already matches the spec's literal name
    verbatim.

Composite index with a ``DESC`` column -- ``(report_definition_id,
started_at DESC)``:
    SQLAlchemy's ``Index()`` accepts plain column-name strings for columns
    in ascending order (the convention every other composite index in this
    codebase uses, e.g. ``invoice``'s own ``(customer_id, state)``), but a
    descending sort order needs an actual SQL expression, not a bare name
    -- there is no plain-string spelling for "this column, descending"
    that ``Index()`` resolves automatically. This model therefore passes
    ``sa_text("started_at DESC")`` as the second index expression alongside
    the plain string ``"report_definition_id"``, the same
    ``sqlalchemy.text()`` escape hatch this codebase's ``postgresql_where``
    partial-index clauses already use elsewhere (e.g.
    ``shipment.idx_shipment_active``), applied here to an index *column*
    expression rather than a *filter* expression. The index's own
    assembled *name* does not encode the ``DESC`` qualifier (matching how
    no other named index in this codebase encodes sort direction in its
    name either) -- ``idx_report_run_report_definition_id_started_at`` via
    ``idx_index_name`` + ``composite_descriptor`` on the plain column-name
    pair.

Out of scope for this model (not implemented here):
    * Range partitioning by ``started_at`` (yearly) -- spec marks this
      conditional on report volume growing large, a future physical-design
      decision.
    * Any Alembic migration.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import state_token_type


class ReportRun(Base, UniversalAuditColumns):
    """``T26 — report_run`` — a generated report instance/output, linked to its stored document (Classification: T)."""

    __tablename__ = "report_run"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------- report_definition_id
    report_definition_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "report_definition.id",
            name=fk_index_name("report_run", "report_definition_id", "report_definition"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------ generated_document_id
    # Nullable -- populated only once the run completes and produces a
    # stored document. See module docstring's dedicated section.
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "generated_document.id",
            name=fk_index_name("report_run", "generated_document_id", "generated_document"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------------ triggered_by
    # This table's own spec'd business actor -- distinct from UAC's mixin
    # created_by. Nullable: spec "NULL = scheduler". See module docstring's
    # dedicated section.
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("report_run", "triggered_by", "app_user"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------------------------ status
    # VARCHAR(16) -- EXACT spec match via state_token_type(). Quoted-string
    # default mirrors notification.state's own pattern and starting value.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
        default="QUEUED",
        server_default=sa_text("'QUEUED'"),
    )

    # -------------------------------------------------------------- started_at
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------ completed_at
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --------------------------------------------------------------- row_count
    # Plain BigInteger -- no database/types.py factory exists for BIGINT.
    # Nullable: unknown until the run reaches a terminal state. See module
    # docstring's dedicated section.
    row_count: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )

    # -------------------------------------------------------------------- deleted_at
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: ReportRunStatus 4-value vocabulary.
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETE', 'FAILED')",
            name=ck_index_name("report_run", "status"),
        ),
        # CHECK: row_count non-negative when present, NULL-safe.
        CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name=ck_index_name("report_run", "row_count_nonneg"),
        ),
        # Recommended single-column index.
        Index(
            idx_index_name("report_run", "report_definition_id"),
            "report_definition_id",
        ),
        # Composite index -- (report_definition_id, started_at DESC). See
        # module docstring's dedicated section on the DESC column
        # expression.
        Index(
            idx_index_name(
                "report_run",
                composite_descriptor(("report_definition_id", "started_at")),
            ),
            "report_definition_id",
            sa_text("started_at DESC"),
        ),
        # Partial index -- active-run polling query.
        Index(
            idx_index_name("report_run", "active"),
            "status",
            postgresql_where=sa_text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )


__all__ = ["ReportRun"]
