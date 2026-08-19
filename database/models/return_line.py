"""
return_line

Source of truth: 06_ERD.md (T28) + 07_DATABASE_SPEC.md (T28 -- return_line).

Purpose (from spec): line-level detail of a return -- product, quantity,
and post-inspection condition/disposition.

Mixin choice: UniversalAuditColumns (UAC).
Rationale: spec marks this table "+UAC" and classifies it T (transactional,
mutable) -- condition/disposition are set post-inspection (in place),
soft-deletable pre-inspection, same audit family as its parent
customer_return (T27).

Same verified conventions as customer_return.py / the rest of this
codebase: no repeated schema=, id via id_column(), inheritance order
(Base, UniversalAuditColumns), __mapper_args__ version_id_col, generic
String/Numeric types, real database/naming.py helpers.

*** FK NAMING (per this batch's explicit new rule) ***
Every ForeignKey() below is given an explicit name=fk_index_name(...) --
see customer_return.py's module docstring for the full rationale
(commission_transaction.py's unguarded self-ref FK naming gap).
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name, idx_index_name, uq_index_name

_TABLE = "return_line"


class ReturnLine(Base, UniversalAuditColumns):
    """Line-level detail of a return: product, quantity, and
    post-inspection condition/disposition.

    ERD id: T28. Classification: T (transactional, mutable).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        # Matches spec verbatim: uq_return_line (customer_return_id,
        # order_line_id) WHERE order_line_id IS NOT NULL -- partial unique,
        # same pattern as uq_physical_count_one_open /
        # uq_approval_request_one_pending.
        Index(
            uq_index_name(_TABLE, "customer_return_id_order_line_id"),
            "customer_return_id",
            "order_line_id",
            unique=True,
            postgresql_where=text("order_line_id IS NOT NULL"),
        ),
        CheckConstraint(
            "qty_returned > 0",
            name=ck_index_name(_TABLE, "qty_positive"),
        ),
        CheckConstraint(
            "condition IS NULL OR condition IN ('SALEABLE','DAMAGED','EXPIRED','QUARANTINE')",
            name=ck_index_name(_TABLE, "condition"),
        ),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('RESTOCK','SCRAP','QUARANTINE')",
            name=ck_index_name(_TABLE, "disposition"),
        ),
        Index(idx_index_name(_TABLE, "product_id"), "product_id"),
    )

    id: GuidPk = id_column()

    customer_return_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customer_return.id",
            name=fk_index_name(_TABLE, "customer_return_id", "customer_return"),
        ),
        nullable=False,
    )
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "order_line.id",
            name=fk_index_name(_TABLE, "order_line_id", "order_line"),
        ),
        nullable=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name(_TABLE, "product_id", "product"),
        ),
        nullable=False,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "product_lot.id",
            name=fk_index_name(_TABLE, "lot_id", "product_lot"),
        ),
        nullable=True,
    )

    qty_returned: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    condition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    unit_refund_amount: Mapped[object] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReturnLine id={self.id} customer_return_id={self.customer_return_id} "
            f"product_id={self.product_id} qty_returned={self.qty_returned}>"
        )
