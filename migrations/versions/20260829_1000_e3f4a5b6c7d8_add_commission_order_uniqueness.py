"""Add partial unique index on commission_transaction(order_id).

Prevents duplicate commission calculations for the same order.
A partial index on (order_id) WHERE order_id IS NOT NULL AND
state_event = 'ACCRUED' ensures at most one ACCRUED commission
per order, while still allowing CLAWED_BACK transactions and
commission records without an order_id.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-29 10:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"
    op.create_index(
        "uq_commission_transaction_order_accrued",
        "commission_transaction",
        ["order_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("order_id IS NOT NULL AND state_event = 'ACCRUED'"),
    )


def downgrade() -> None:
    schema = "erp"
    op.drop_index(
        "uq_commission_transaction_order_accrued",
        table_name="commission_transaction",
        schema=schema,
    )
