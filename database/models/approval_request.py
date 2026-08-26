"""
approval_request

Source of truth: 06_ERD.md (T25) + 07_DATABASE_SPEC.md (T25 -- approval_request).

NOTE ON NUMBERING: the originating task calls this table "T21", but in the
project's own 06_ERD.md / 07_DATABASE_SPEC.md it is documented as **T25**.
Implemented here by name/spec, not by that local task number.

Purpose (from spec): a live approval task raised against any approvable
entity (order override, stock adjustment above threshold, manual price
override, credit note, customer_return, ...). Polymorphic target via
(entity_type, entity_id).

Mixin choice: UniversalAuditColumns (UAC).
Rationale (per spec body + column table, which explicitly marks this table
"+UAC"): approval_request is a genuinely mutable transactional (T-classified)
row -- status moves PENDING -> APPROVED/REJECTED/CANCELLED in place, it is
soft-deletable for erroneous/cancelled requests, and it is NOT append-only
itself. The append-only trail of its transitions lives in the companion
approval_history (H7) table, which uses AppendOnlyAuditColumns (AAC) instead.
This mirrors how stock_adjustment / order / stock_transfer (all UAC) each
have their own *_history companion (AAC).

Verified against the real project modules (database/base.py,
database/mixins.py, database/naming.py, as uploaded):
  * schema is NOT repeated in __table_args__ -- Base.metadata already carries
    schema="erp" (database.constants.APP_SCHEMA).
  * id: GuidPk = id_column() -- the PK is added explicitly per model, it is
    NOT part of either mixin.
  * Inheritance order is (Base, UniversalAuditColumns), per mixins.py's own
    usage example.
  * FK target strings are unqualified ("app_user.id"), matching
    UniversalAuditColumns.created_by/updated_by's own style -- schema
    resolution happens via the shared MetaData, not a literal "erp." prefix.
  * UUID columns use sqlalchemy.Uuid(as_uuid=True) (as base.py/mixins.py do),
    not sqlalchemy.dialects.postgresql.UUID.
  * UAC's optimistic-lock `version` column requires the concrete model to
    opt in via __mapper_args__ = {"version_id_col": "version"} (mixins.py
    docstring: "the mixin only supplies the column").
  * Constraint/index names are built with the real database/naming.py
    helpers (ck_index_name / uq_index_name / idx_index_name); FK naming is
    left to NAMING_CONVENTION's automatic "fk" template.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, idx_index_name, uq_index_name

_TABLE = "approval_request"


class ApprovalRequest(Base, UniversalAuditColumns):
    """A live approval task raised against any approvable entity.

    ERD id: T25. Classification: T (transactional, mutable -- see module
    docstring for the UAC-vs-AAC rationale).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        # Matches spec verbatim: ck_approval_request_status.
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED')",
            name=ck_index_name(_TABLE, "status"),
        ),
        # Matches spec verbatim: ck_approval_request_separation_of_duties.
        CheckConstraint(
            "assigned_approver_id IS DISTINCT FROM requested_by",
            name=ck_index_name(_TABLE, "separation_of_duties"),
        ),
        # Business rule: exactly one PENDING approval_request per
        # (entity_type, entity_id) at a time. Partial unique index, same
        # pattern as uq_physical_count_one_open on physical_count. Matches
        # spec verbatim: uq_approval_request_one_pending.
        Index(
            uq_index_name(_TABLE, "one_pending"),
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Matches spec verbatim: idx_approval_request_pending_queue.
        Index(
            idx_index_name(_TABLE, "pending_queue"),
            "assigned_approver_id",
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Recommended composite index (entity_type, entity_id) -- spec lists
        # it under "Composite Indexes" without a bespoke name.
        Index(
            idx_index_name(_TABLE, "entity"),
            "entity_type",
            "entity_id",
        ),
    )

    id: GuidPk = id_column()

    # --- polymorphic target -------------------------------------------------
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    # --- actors ---------------------------------------------------------
    # Explicit FK column types throughout (Uuid) to avoid the NullType
    # inference bug when the target table isn't imported/registered yet.
    requested_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=False,
    )
    assigned_approver_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )

    # --- human-readable reference ------------------------------------------
    # APR-XXXXXXXX format for Telegram bot UX.  Generated at creation time.
    approval_number: Mapped[str | None] = mapped_column(
        String(40), nullable=True, unique=True,
    )

    # --- state ------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    threshold_marker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # --- payload (deferred execution data) ----------------------------------
    # JSON payload storing the serialized command data for deferred execution.
    # Per ADR-008 §6, approval_required=True commands store their execution
    # data here so the mutation can be replayed after approval.
    payload: Mapped[dict | None] = mapped_column(
        JSON(),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ApprovalRequest id={self.id} entity={self.entity_type}:{self.entity_id} "
            f"status={self.status}>"
        )
