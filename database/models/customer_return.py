"""
customer_return

Source of truth: 06_ERD.md (T27) + 07_DATABASE_SPEC.md (T27 -- customer_return).

Purpose (from spec): header for a physical return event -- customer return,
rep return-to-factory, or damaged return.

Mixin choice: UniversalAuditColumns (UAC).
Rationale: spec marks this table "+UAC" and classifies it T (transactional,
mutable) -- state moves PENDING_APPROVAL -> APPROVED -> RECEIVED ->
INSPECTED -> CLOSED/REJECTED in place, soft-deletable pre-CLOSED.

Same verified conventions as the rest of this codebase: no repeated schema=
(Base.metadata already carries schema="erp"), id via id_column(),
inheritance order (Base, UniversalAuditColumns), __mapper_args__ =
{"version_id_col": "version"} (UAC's optimistic lock), generic
String/DateTime types, real database/naming.py helpers for constraint/index
names.

*** FK NAMING (per this batch's explicit new rule) ***
Every ForeignKey() below is given an explicit name=fk_index_name(...)
rather than being left to NAMING_CONVENTION["fk"]'s automatic template.
This was flagged as a real, demonstrated gap in the prior batch
(commission_transaction.py's self-referencing reversal_of_id FK silently
produces a 69-character name via the automatic path, over PostgreSQL's
63-char limit, since NAMING_CONVENTION's automatic "fk" substitution is NOT
covered by naming.py's own _enforce_length_limit() guard -- that guard only
wraps the hand-authored fk_index_name()/uq_index_name()/etc. helpers, a gap
naming.py's own docstring documents explicitly under "KNOWN GAP"). None of
this table's FK names would have actually exceeded 63 chars even
unguarded, but explicit naming is applied uniformly per the new rule rather
than selectively.

Business Constraints note: the spec's "exactly one of customer_id /
representative_id populated, matching return_type" rule needs a
BEFORE INSERT/UPDATE trigger (the value-dependent combination cannot be
expressed as a plain CHECK) -- out of scope for the ORM layer; only the
weaker "at least one of the two" half is expressed here as
ck_customer_return_party, matching the spec's own Check Constraints list
verbatim.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name, uq_index_name

_TABLE = "customer_return"


class CustomerReturn(Base, UniversalAuditColumns):
    """Header for a physical return event.

    ERD id: T27. Classification: T (transactional, mutable).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        # Matches spec verbatim: uq_customer_return_number.
        Index(
            uq_index_name(_TABLE, "number"),
            "return_number",
            unique=True,
        ),
        CheckConstraint(
            "return_type IN ('CUSTOMER_RETURN','REP_RETURN_TO_FACTORY','DAMAGED_RETURN')",
            name=ck_index_name(_TABLE, "type"),
        ),
        CheckConstraint(
            "state IN ('PENDING_APPROVAL','APPROVED','RECEIVED','INSPECTED','CLOSED','REJECTED')",
            name=ck_index_name(_TABLE, "state"),
        ),
        # Weaker "at least one" half of the spec's business constraint; see
        # module docstring for the stronger, trigger-only, value-dependent
        # half.
        CheckConstraint(
            "customer_id IS NOT NULL OR representative_id IS NOT NULL",
            name=ck_index_name(_TABLE, "party"),
        ),
        Index(idx_index_name(_TABLE, "customer_id"), "customer_id"),
        Index(idx_index_name(_TABLE, "representative_id"), "representative_id"),
        Index(idx_index_name(_TABLE, "warehouse_id"), "warehouse_id"),
        # Composite index per spec: (warehouse_id, state) -- receiving-dock
        # operations queue.
        Index(
            idx_index_name(_TABLE, "warehouse_state"),
            "warehouse_id",
            "state",
        ),
        # Matches spec verbatim: idx_customer_return_open.
        Index(
            idx_index_name(_TABLE, "open"),
            "warehouse_id",
            postgresql_where=text("state NOT IN ('CLOSED','REJECTED')"),
        ),
    )

    id: GuidPk = id_column()

    return_number: Mapped[str] = mapped_column(String(40), nullable=False)

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "order.id",
            name=fk_index_name(_TABLE, "order_id", "order"),
        ),
        nullable=True,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name(_TABLE, "customer_id", "customer"),
        ),
        nullable=True,
    )
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name(_TABLE, "representative_id", "representative"),
        ),
        nullable=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name(_TABLE, "warehouse_id", "warehouse"),
        ),
        nullable=False,
    )
    initiated_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name(_TABLE, "initiated_by", "app_user"),
        ),
        nullable=False,
    )
    reason_code_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "reason_code_ref.id",
            name=fk_index_name(_TABLE, "reason_code_id", "reason_code_ref"),
        ),
        nullable=False,
    )

    return_type: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING_APPROVAL'")
    )
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    received_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CustomerReturn id={self.id} number={self.return_number} "
            f"type={self.return_type} state={self.state}>"
        )
