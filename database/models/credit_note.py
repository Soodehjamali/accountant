"""
credit_note

Source of truth: 06_ERD.md (T20) + 07_DATABASE_SPEC.md (T20 -- credit_note).

IMPORTANT DISAMBIGUATION (per this task's own note, verified against the
spec): T20 is credit_note. A prior batch's models_init.py entry for
payment_allocation is labeled "PaymentAllocation (T20/J2 -- ...)" --  that
"T20" in payment_allocation's own docstring is a pre-existing mislabel
already present in this codebase (payment_allocation is really J2 only).
This is not something this pass touches or fixes (out of scope -- not one
of the tables asked for here); flagging it only so the two "T20"s in the
codebase's docstrings aren't confused with each other. credit_note is a
wholly separate table.

Purpose (from spec): formal correction instrument against a
closed/issued invoice (BR-F3, EC11, SRS E22).

Mixin choice: UniversalAuditColumns (UAC).
Rationale: spec marks this table "+UAC" and classifies it T (transactional,
mutable) -- state moves DRAFT -> ISSUED -> APPLIED/VOID in place on the same
row, and it is soft-deletable pre-ISSUED only.

Same verified conventions as the other models built in this codebase: no
repeated schema= (Base.metadata already carries schema="erp"), id via
id_column(), inheritance order (Base, UniversalAuditColumns),
__mapper_args__ = {"version_id_col": "version"} (UAC's optimistic lock),
unqualified FK target strings, sqlalchemy.Uuid(as_uuid=True), generic
String/DateTime/Numeric types, real database/naming.py helpers.

reference_type/reference_id (polymorphic, nullable) intentionally carries no
ForeignKey() -- same treatment as attachment.entity_id /
generated_document.entity_id / audit_log's own (entity_type, entity_id)
elsewhere in this codebase (see spec's Global Standards -> Polymorphic
Reference Policy).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, idx_index_name, uq_index_name

_TABLE = "credit_note"


class CreditNote(Base, UniversalAuditColumns):
    """Formal correction instrument against a closed/issued invoice.

    ERD id: T20. Classification: T (transactional, mutable).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        # Matches spec verbatim: uq_credit_note_number. Hand-authored name
        # (not the automatic single-column "uq" convention template) since
        # the column itself already repeats the table name
        # (credit_note_number) -- the automatic convention would otherwise
        # double it to uq_credit_note_credit_note_number.
        UniqueConstraint(
            "credit_note_number",
            name=uq_index_name(_TABLE, "number"),
        ),
        CheckConstraint(
            "state IN ('DRAFT','ISSUED','APPLIED','VOID')",
            name=ck_index_name(_TABLE, "state"),
        ),
        CheckConstraint(
            "total_amount > 0",
            name=ck_index_name(_TABLE, "amount_positive"),
        ),
        Index(idx_index_name(_TABLE, "invoice_id"), "invoice_id"),
        Index(idx_index_name(_TABLE, "customer_id"), "customer_id"),
        # Composite index per spec: (reference_type, reference_id) -- trace
        # back to the originating return/adjustment.
        Index(
            idx_index_name(_TABLE, "reference"),
            "reference_type",
            "reference_id",
        ),
    )

    id: GuidPk = id_column()

    credit_note_number: Mapped[str] = mapped_column(String(40), nullable=False)

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("invoice.id"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customer.id"),
        nullable=False,
    )
    issued_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=False,
    )
    reason_code_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reason_code_ref.id"),
        nullable=False,
    )

    # --- polymorphic reference; no FK by design, see module docstring -----
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    total_amount: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DRAFT'")
    )
    issued_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CreditNote id={self.id} number={self.credit_note_number} "
            f"state={self.state}>"
        )
