"""
kpi_snapshot

Source of truth: 06_ERD.md (H10) + 07_DATABASE_SPEC.md (H10 -- kpi_snapshot).

Purpose (from spec): periodic immutable capture of headline KPIs (total
stock value, open order count, commission payable, AR balance, etc.) for
dashboards/trend charts, decoupled from live query load on the operational
ledgers.

Mixin choice: AppendOnlyAuditColumns (AAC).
Rationale: spec marks this table "+AAC" and classifies it H (append-only
history) -- populated exclusively by a scheduled job, never hand-edited;
enforced via REVOKE INSERT ... FROM app_role except the reporting-job role
per the spec's Business Constraints.

Same verified conventions as the other new models in this pass: no
repeated schema=, id via id_column(), inheritance order
(Base, AppendOnlyAuditColumns), unqualified FK target strings,
sqlalchemy.Uuid(as_uuid=True), generic String/DateTime/Numeric types, real
database/naming.py helpers.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, idx_index_name, uq_index_name

_TABLE = "kpi_snapshot"


class KpiSnapshot(Base, AppendOnlyAuditColumns):
    """Periodic immutable capture of headline KPIs for dashboards/trend charts.

    ERD id: H10. Classification: H (append-only history).
    """

    __tablename__ = _TABLE
    __table_args__ = (
        # Matches spec verbatim: ck_kpi_snapshot_scope_type,
        # ck_kpi_snapshot_granularity, ck_kpi_snapshot_scope_consistency.
        CheckConstraint(
            "scope_type IN ('GLOBAL','WAREHOUSE','REPRESENTATIVE')",
            name=ck_index_name(_TABLE, "scope_type"),
        ),
        CheckConstraint(
            "period_granularity IN ('DAILY','WEEKLY','MONTHLY')",
            name=ck_index_name(_TABLE, "granularity"),
        ),
        CheckConstraint(
            "scope_type <> 'GLOBAL' OR scope_id IS NULL",
            name=ck_index_name(_TABLE, "scope_consistency"),
        ),
        # Matches spec verbatim: uq_kpi_snapshot (spec gives this
        # constraint no extra descriptor suffix, unlike most other uq_
        # names in the document -- empty descriptor collapses to the bare
        # "uq_kpi_snapshot" via naming.py's _join()).
        Index(
            uq_index_name(_TABLE, ""),
            "kpi_key",
            "scope_type",
            "scope_id",
            "captured_at",
            "period_granularity",
            unique=True,
        ),
        Index(idx_index_name(_TABLE, "kpi_key"), "kpi_key"),
        # Composite index per spec: (kpi_key, scope_type, scope_id,
        # captured_at DESC) -- dashboard trend-chart query.
        Index(
            idx_index_name(_TABLE, "trend_query"),
            "kpi_key",
            "scope_type",
            "scope_id",
            "captured_at",
        ),
    )

    id: GuidPk = id_column()

    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("warehouse.id"),
        nullable=True,
    )
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("representative.id"),
        nullable=True,
    )

    kpi_key: Mapped[str] = mapped_column(String(60), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Denormalized copy of whichever of warehouse_id/representative_id
    # applies, or NULL for GLOBAL -- per spec Notes, kept in sync by the
    # same job that writes the row, never edited afterward. Not a real FK
    # (its target table varies with scope_type), same polymorphic-style
    # treatment as entity_id in audit_log/approval_request.
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    value: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    period_granularity: Mapped[str] = mapped_column(String(10), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<KpiSnapshot id={self.id} kpi_key={self.kpi_key} "
            f"scope={self.scope_type}:{self.scope_id} value={self.value}>"
        )
