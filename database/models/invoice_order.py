"""
invoice_order

Source of truth: 06_ERD.md (J1) + 07_DATABASE_SPEC.md (J1 -- invoice_order,
Junction). Transcribed verbatim:

    1. Purpose: Resolves N:N between invoice and order (split/consolidated
       invoicing).
    2. Primary Key: composite (invoice_id, order_id)
    3. Foreign Keys: invoice_id -> invoice.id; order_id -> order.id
    4. Column Definitions: invoice_id UUID NOT NULL; order_id UUID NOT NULL;
       linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
    5. Unique Constraints: the PK itself
    6. Check Constraints: none
    7. Business Constraints: none beyond referential integrity
    8. Recommended Indexes: btree on order_id
    9. Composite Indexes: PK doubles as the invoice -> orders forward lookup
    10. Partial Indexes: none
    11. Partitioning: none
    12. Soft Delete: none -- pure resolving join; unlinking (rare,
        correction-only) is a hard DELETE mirrored to audit_log
    13. Audit Strategy: link/unlink events written to audit_log
    14. Row growth: tracks invoice x orders-per-invoice, ~1:1
    15. Notes: --

Mixin choice: NONE. Confirmed directly against the Column Definitions list
above -- it carries no "+UAC"/"+AAC" marker, just the three bare columns.
Same "bare junction" pattern already established in this codebase by
J2/payment_allocation (models_init.py's own note: "no audit mixin, per
spec's own bare column list"). No surrogate id either -- the PK is the
composite (invoice_id, order_id) itself.

Table-name note (`order` is a SQL reserved word): this project's own
check_mappers.py already looks up ``Base.metadata.tables["erp.order"]``
directly (e.g. its ``("order", "created_by", "app_user")`` spot-check), so
the physical ``__tablename__`` for the Order model is confirmed to be
literally "order" -- ``ForeignKey("order.id")`` below is not a guess.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.naming import idx_index_name

_TABLE = "invoice_order"


class InvoiceOrder(Base):
    """Resolving N:N junction between invoice and order.

    ERD id: J1. Classification: J (junction, no audit mixin -- see module
    docstring).
    """

    __tablename__ = _TABLE
    __table_args__ = (
        # Matches spec's Recommended Indexes: btree on order_id. The
        # composite PK (invoice_id, order_id) already covers invoice_id
        # lookups (leading column) but not order_id alone.
        Index(idx_index_name(_TABLE, "order_id"), "order_id"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("invoice.id"),
        primary_key=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("order.id"),
        primary_key=True,
    )
    linked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InvoiceOrder invoice_id={self.invoice_id} order_id={self.order_id}>"
