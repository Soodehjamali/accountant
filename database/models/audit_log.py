"""
audit_log

Source of truth: 06_ERD.md (H6) + 07_DATABASE_SPEC.md (H6 -- audit_log).

Purpose (from spec): system-wide immutable audit trail covering every
state-changing action across the whole system. Complements (does not
duplicate) the entity-specific *_history tables -- this table captures the
raw actor/before/after for every mutating action, system-wide.

Mixin choice: AppendOnlyAuditColumns (AAC).
Rationale: spec marks this table "+AAC" and classifies it H (append-only
history) -- no UPDATE/DELETE at the permissions layer per ERD Section 0.3,
consistent with inventory_transaction / *_history / price_history / etc.

Same verified conventions as approval_request.py / approval_history.py /
notification.py: no repeated schema= (Base.metadata already carries
schema="erp"), id via id_column(), inheritance order (Base,
AppendOnlyAuditColumns), unqualified FK target strings, sqlalchemy.Uuid(as_uuid=True),
generic String/DateTime types, real database/naming.py helpers for
constraint/index names.

NOTE: the polymorphic (entity_type, entity_id) pair has no DB-level FK
enforcement by design -- per the spec's own Notes: "The polymorphic
(entity_type, entity_id) pattern here has no DB-level FK enforcement -- see
Global Standards -> Polymorphic Reference Policy for the accepted trade-off
and mitigation (composite index + optional per-type validation trigger)."
This mirrors attachment.entity_id and generated_document.entity_id, which
your own check_mappers.py explicitly excludes from its FK spot-checks for
the same reason.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, idx_index_name

_TABLE = "audit_log"


class AuditLog(Base, AppendOnlyAuditColumns):
    """System-wide immutable audit trail covering every state-changing action.

    ERD id: H6. Classification: H (append-only history).
    """

    __tablename__ = _TABLE
    __table_args__ = (
        # Matches spec verbatim: ck_audit_log_action. Spec explicitly notes
        # this list is extended at migration time as concrete actions are
        # enumerated by the application -- kept exactly as documented, not
        # expanded speculatively here.
        CheckConstraint(
            "action IN ('CREATE','UPDATE','DELETE','APPROVE','REJECT','OVERRIDE')",
            name=ck_index_name(_TABLE, "action"),
        ),
        Index(idx_index_name(_TABLE, "actor_user_id"), "actor_user_id"),
        # Composite index per spec: (entity_type, entity_id, occurred_at) --
        # "the dominant 'show me this record's history' query".
        Index(
            idx_index_name(_TABLE, "entity_occurred"),
            "entity_type",
            "entity_id",
            "occurred_at",
        ),
    )

    id: GuidPk = id_column()

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )

    # --- polymorphic target; no FK by design, see module docstring --------
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    action: Mapped[str] = mapped_column(String(20), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuditLog id={self.id} entity={self.entity_type}:{self.entity_id} "
            f"action={self.action}>"
        )
