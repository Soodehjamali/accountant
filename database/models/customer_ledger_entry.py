"""
customer_ledger_entry

Source of truth: 06_ERD.md (T22) + 07_DATABASE_SPEC.md (T22 --
customer_ledger_entry).

DISAMBIGUATION (per this task's own note, verified against the spec):
customer_ledger_entry (T22) is the actual, authoritative accounts-receivable
ledger -- the append-only source of truth for customer balances.
customer_ledger (M13, already built in an earlier batch as CustomerLedger)
is a *different* table: a non-authoritative, read-optimized 1:1 cache header
per customer (current_balance, last_reconciled_at, last_entry_seq), rebuilt
entirely from this table. customer_ledger_entry.customer_ledger_id is an FK
*to that M13 cache header*, not to customer directly -- confirmed from the
spec's own Foreign Keys line below.

Purpose (from spec): immutable, append-only accounts-receivable ledger --
the actual source of truth for customer balances.

Mixin choice: AppendOnlyAuditColumns (AAC).
Rationale: spec marks this table "+AAC" and classifies it H-pattern
(ledger, event-sourced) -- explicitly "Append-only, no UPDATE/DELETE;
corrections are a compensating entry only; hash-chain integrity mirrors T1"
(inventory_transaction). Same event-sourced-ledger family as T1/T23.

Same verified conventions as the rest of this codebase: no repeated
schema=, id via id_column(), inheritance order (Base, AppendOnlyAuditColumns),
unqualified FK target strings, sqlalchemy.Uuid(as_uuid=True), generic
String/DateTime/Numeric/CHAR types, real database/naming.py helpers.

reference_type/reference_id (polymorphic: invoice | payment | credit_note)
intentionally carries no ForeignKey() -- same treatment as audit_log's own
(entity_type, entity_id) and credit_note's (reference_type, reference_id)
elsewhere in this codebase.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CHAR,
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
from database.naming import ck_index_name, idx_index_name, uq_index_name

_TABLE = "customer_ledger_entry"


class CustomerLedgerEntry(Base, AppendOnlyAuditColumns):
    """Immutable, append-only accounts-receivable ledger.

    ERD id: T22. Classification: H-pattern (event-sourced ledger, append-only).
    """

    __tablename__ = _TABLE
    __table_args__ = (
        # Matches spec verbatim: uq_customer_ledger_entry_seq,
        # uq_customer_ledger_entry_hash.
        UniqueConstraint(
            "customer_ledger_id",
            "sequence_no",
            name=uq_index_name(_TABLE, "seq"),
        ),
        UniqueConstraint(
            "row_hash",
            name=uq_index_name(_TABLE, "hash"),
        ),
        CheckConstraint(
            "entry_type IN ('INVOICE_ISSUED','PAYMENT_RECEIVED','CREDIT_NOTE_APPLIED','WRITE_OFF')",
            name=ck_index_name(_TABLE, "type"),
        ),
        CheckConstraint(
            "signed_amount <> 0",
            name=ck_index_name(_TABLE, "amount_nonzero"),
        ),
        Index(
            idx_index_name(_TABLE, "reference"),
            "reference_type",
            "reference_id",
        ),
        # Composite index per spec: (customer_ledger_id, sequence_no) --
        # the balance-projection query.
        Index(
            idx_index_name(_TABLE, "ledger_seq"),
            "customer_ledger_id",
            "sequence_no",
        ),
    )

    id: GuidPk = id_column()

    customer_ledger_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customer_ledger.id"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=False,
    )

    # --- polymorphic reference; no FK by design, see module docstring -----
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signed_amount: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    currency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("currency.id"),
        nullable=False,
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    row_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CustomerLedgerEntry id={self.id} "
            f"customer_ledger_id={self.customer_ledger_id} seq={self.sequence_no} "
            f"amount={self.signed_amount}>"
        )
