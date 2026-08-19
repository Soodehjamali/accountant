"""
approval_history

Source of truth: 06_ERD.md (H7) + 07_DATABASE_SPEC.md (H7 -- approval_history).

NOTE ON NUMBERING: the originating task calls this table "T22", but in the
project's own ERD/DB-SPEC it is documented as **H7**. See approval_request.py
for the same note.

Purpose (from spec): immutable log of every status transition on an
approval_request -- supersedes tracking via a single mutable status field.

Mixin choice: AppendOnlyAuditColumns (AAC).
Rationale: the spec explicitly marks this table "+AAC" and classifies it H
(History). No UPDATE/DELETE at the permissions layer, no soft delete, no
optimistic-concurrency `version` column -- this is a pure chronological
append log, same pattern as order_status_history / transfer_history /
shipment_status_history / invoice_history / notification_history.

Verified against the real project modules -- same conventions documented in
approval_request.py's docstring apply here: no repeated schema=, id via
id_column(), inheritance order (Base, AppendOnlyAuditColumns), unqualified FK
target strings, sqlalchemy.Uuid(as_uuid=True), and real database/naming.py
helpers for constraint/index names. AAC has no `version` column, so no
__mapper_args__ version_id_col here (only UAC-based models need that).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, idx_index_name

_TABLE = "approval_history"

# ApprovalStatus enum values, per 06_ERD.md PART A.
_APPROVAL_STATUS_VALUES = "'PENDING','APPROVED','REJECTED','CANCELLED'"


class ApprovalHistory(Base, AppendOnlyAuditColumns):
    """Immutable, append-only log of approval_request status transitions.

    ERD id: H7. Classification: H (append-only history).
    """

    __tablename__ = _TABLE
    __table_args__ = (
        # Matches spec verbatim: ck_approval_history_statuses.
        CheckConstraint(
            f"from_status IN ({_APPROVAL_STATUS_VALUES}) "
            f"AND to_status IN ({_APPROVAL_STATUS_VALUES})",
            name=ck_index_name(_TABLE, "statuses"),
        ),
        Index(idx_index_name(_TABLE, "request_id"), "approval_request_id"),
        # Composite index per spec: (approval_request_id, event_at).
        Index(
            idx_index_name(_TABLE, "request_event"),
            "approval_request_id",
            "event_at",
        ),
    )

    id: GuidPk = id_column()

    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_request.id"),
        nullable=False,
    )
    # NOTE: the spec marks approval_history.actor_user_id NOT NULL (unlike,
    # e.g., shipment_status_history.actor_user_id which is nullable for
    # automated pings) -- this is a real FK here, kept NOT NULL per spec.
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=False,
    )

    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    event_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ApprovalHistory id={self.id} request={self.approval_request_id} "
            f"{self.from_status}->{self.to_status}>"
        )
