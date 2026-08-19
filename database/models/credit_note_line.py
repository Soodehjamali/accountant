"""
credit_note_line

Source of truth: 06_ERD.md (T21) + 07_DATABASE_SPEC.md (T21 -- credit_note_line).

Purpose (from spec): line items of a credit note, mirroring the invoice
lines being corrected.

Mixin choice: UniversalAuditColumns (UAC).
Rationale: spec marks this table "+UAC" and classifies it T (transactional,
mutable) -- soft-deletable pre-issue, same audit family as its parent
credit_note (T20).

Same verified conventions as credit_note.py / the rest of this codebase: no
repeated schema=, id via id_column(), inheritance order
(Base, UniversalAuditColumns), __mapper_args__ version_id_col, unqualified
FK target strings, sqlalchemy.Uuid(as_uuid=True), generic String/Numeric
types, real database/naming.py helpers.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, idx_index_name

_TABLE = "credit_note_line"


class CreditNoteLine(Base, UniversalAuditColumns):
    """Line items of a credit note, mirroring the invoice lines being corrected.

    ERD id: T21. Classification: T (transactional, mutable).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        # Matches spec verbatim: ck_credit_note_line_qty_positive.
        CheckConstraint(
            "qty > 0",
            name=ck_index_name(_TABLE, "qty_positive"),
        ),
        Index(idx_index_name(_TABLE, "invoice_line_id"), "invoice_line_id"),
    )

    id: GuidPk = id_column()

    credit_note_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("credit_note.id"),
        nullable=False,
    )
    invoice_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("invoice_line.id"),
        nullable=True,
    )

    description: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    # Application-computed (qty * unit_price), per spec's Notes.
    line_total: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CreditNoteLine id={self.id} credit_note_id={self.credit_note_id} "
            f"line_total={self.line_total}>"
        )
