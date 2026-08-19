"""
notification

Source of truth: 06_ERD.md (T24) + 07_DATABASE_SPEC.md (T24 -- notification).

NOTE ON NUMBERING: the originating task calls this table "T23", but in the
project's own ERD/DB-SPEC it is documented as **T24**. See approval_request.py
for the same note.

Purpose (from spec): outbound/internal notification record generated for a
user or a representative (SRS E32, S4 Notification Service).

Mixin choice: UniversalAuditColumns (UAC).
Rationale: spec marks this table "+UAC" and classifies it T (transactional,
mutable) -- `state` transitions QUEUED -> SENT/FAILED/ACKNOWLEDGED in place
on the same row (retry_count is incremented, sent_at/acknowledged_at are
back-filled), and it is soft-deletable per a retention policy. The
append-only trail of its state transitions lives in the companion
notification_history (H8) table (out of scope for this pass), which would
use AppendOnlyAuditColumns -- same UAC/AAC split as
approval_request/approval_history.

Verified against the real project modules -- same conventions documented in
approval_request.py's docstring apply here, plus __mapper_args__ =
{"version_id_col": "version"} since this is a UAC-based model. JSONB import
kept as sqlalchemy.dialects.postgresql.JSONB since JSONB itself is a
Postgres-specific type (base.py/mixins.py only demonstrate generic types
because neither of them happens to need JSONB).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, idx_index_name

_TABLE = "notification"


class Notification(Base, UniversalAuditColumns):
    """Outbound/internal notification record for a user or a representative.

    ERD id: T24. Classification: T (transactional, mutable).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        # Names match 07_DATABASE_SPEC.md verbatim: ck_notification_channel,
        # ck_notification_state, ck_notification_retry_nonneg.
        CheckConstraint(
            "channel IN ('IN_APP','EMAIL','BOT_PUSH','SMS')",
            name=ck_index_name(_TABLE, "channel"),
        ),
        CheckConstraint(
            "state IN ('QUEUED','SENT','FAILED','ACKNOWLEDGED')",
            name=ck_index_name(_TABLE, "state"),
        ),
        CheckConstraint(
            "retry_count >= 0",
            name=ck_index_name(_TABLE, "retry_nonneg"),
        ),
        Index(idx_index_name(_TABLE, "recipient_user_id"), "recipient_user_id"),
        Index(
            idx_index_name(_TABLE, "recipient_representative_id"),
            "recipient_representative_id",
        ),
        # Delivery worker's polling query. Matches spec verbatim:
        # idx_notification_queued.
        Index(
            idx_index_name(_TABLE, "queued"),
            "state",
            "queued_at",
            postgresql_where=text("state = 'QUEUED'"),
        ),
    )

    id: GuidPk = id_column()

    notification_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_type_ref.id"),
        nullable=False,
    )
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )
    recipient_representative_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("representative.id"),
        nullable=True,
    )

    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'QUEUED'")
    )
    queued_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    sent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification id={self.id} channel={self.channel} state={self.state}>"
