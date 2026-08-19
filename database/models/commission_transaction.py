"""
commission_transaction

Source of truth: 06_ERD.md (T23) + 07_DATABASE_SPEC.md (T23 --
commission_transaction).

Purpose (from spec): event-sourced commission ledger per representative --
accrual, approval, payment, clawback are each their own immutable row.

Mixin choice: AppendOnlyAuditColumns (AAC).
Rationale: spec marks this table "+AAC" and classifies it H-pattern
(event-sourced ledger, append-only) -- same family as
inventory_transaction (T1) and customer_ledger_entry (T22); payable
commission is a SUM(signed_amount) projection over this table, never a
mutated running total.

Self-referencing FK: reversal_of_id -> commission_transaction.id (nullable),
set only on CLAWED_BACK rows referencing the original entry being reversed
-- same self-ref pattern as inventory_transaction.reversal_of_id.

Same verified conventions as the rest of this codebase: no repeated
schema=, id via id_column(), inheritance order (Base, AppendOnlyAuditColumns),
unqualified FK target strings, sqlalchemy.Uuid(as_uuid=True), generic
String/DateTime/Numeric types, real database/naming.py helpers.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name, uq_index_name

_TABLE = "commission_transaction"


class CommissionTransaction(Base, AppendOnlyAuditColumns):
    """Event-sourced commission ledger per representative.

    ERD id: T23. Classification: H-pattern (event-sourced ledger, append-only).
    """

    __tablename__ = _TABLE
    __table_args__ = (
        # Matches spec verbatim: uq_commission_transaction_seq.
        UniqueConstraint(
            "representative_id",
            "sequence_no",
            name=uq_index_name(_TABLE, "seq"),
        ),
        CheckConstraint(
            "state_event IN ('ACCRUED','APPROVED','PAID','CLAWED_BACK')",
            name=ck_index_name(_TABLE, "state"),
        ),
        CheckConstraint(
            "signed_amount <> 0",
            name=ck_index_name(_TABLE, "amount_nonzero"),
        ),
        CheckConstraint(
            "state_event <> 'CLAWED_BACK' OR signed_amount < 0",
            name=ck_index_name(_TABLE, "clawback_negative"),
        ),
        Index(idx_index_name(_TABLE, "order_id"), "order_id"),
        # Composite index per spec: (representative_id, sequence_no) -- the
        # balance-projection query.
        Index(
            idx_index_name(_TABLE, "rep_seq"),
            "representative_id",
            "sequence_no",
        ),
    )

    id: GuidPk = id_column()

    representative_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("representative.id"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("order.id"),
        nullable=True,
    )
    # name= is required here (not left to the automatic NAMING_CONVENTION):
    # "commission_transaction" + "commission_config_id" + "commission_config"
    # exceeds PostgreSQL's 63-char identifier limit, and only fk_index_name's
    # own _enforce_length_limit guard shortens it deterministically -- the
    # bare NAMING_CONVENTION template has no such guard.
    commission_config_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "commission_config.id",
            name=fk_index_name(_TABLE, "commission_config_id", "commission_config"),
        ),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name(_TABLE, "actor_user_id", "app_user"),
        ),
        nullable=False,
    )
    # Self-referencing FK; set only on CLAWED_BACK rows. Same
    # over-63-chars situation as commission_config_id above -- explicit
    # name= via fk_index_name is required, same treatment
    # inventory_transaction.reversal_of_id already established (see that
    # helper's own docstring, which uses this exact table/column as its
    # example).
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "commission_transaction.id",
            name=fk_index_name(_TABLE, "reversal_of_id", "commission_transaction"),
        ),
        nullable=True,
    )

    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signed_amount: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    state_event: Mapped[str] = mapped_column(String(16), nullable=False)
    rate_applied: Mapped[object] = mapped_column(Numeric(7, 4), nullable=False)
    currency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("currency.id"),
        nullable=False,
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CommissionTransaction id={self.id} "
            f"representative_id={self.representative_id} seq={self.sequence_no} "
            f"state_event={self.state_event} amount={self.signed_amount}>"
        )
