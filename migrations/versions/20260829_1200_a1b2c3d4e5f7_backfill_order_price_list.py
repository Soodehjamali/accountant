"""Backfill order.price_list_id from order lines' price history.

Every legacy order has order lines with a price_history_id (NOT NULL).
Each price_history row has a price_list_id (NOT NULL).  This migration
derives the correct price_list_id for each legacy order by following
the chain: order_line.price_history_id -> price_history.price_list_id.

After backfill, the column is made NOT NULL.

Revision ID: b1c2d3e4f5a6
Revises: f5a6b7c8d9e0
Create Date: 2026-08-29 12:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"

    # Backfill strategy:
    # For each order with price_list_id IS NULL, derive it from the
    # order's first order line's price_history -> price_history.price_list_id.
    #
    # This is deterministic because:
    # 1. Every order_line has a price_history_id (NOT NULL FK)
    # 2. Every price_history row has a price_list_id (NOT NULL FK)
    # 3. All lines on a given order reference the same price list
    #    (the order's price list is set at creation time)
    op.execute(
        sa.text(
            f"""
            UPDATE erp."order" o
            SET price_list_id = (
                SELECT ph.price_list_id
                FROM erp.order_line ol
                JOIN erp.price_history ph ON ph.id = ol.price_history_id
                WHERE ol.order_id = o.id
                ORDER BY ol.created_at
                LIMIT 1
            )
            WHERE o.price_list_id IS NULL
            """
        )
    )

    # Verify no NULL values remain.
    result = op.get_bind().execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM erp."order"
            WHERE price_list_id IS NULL
            """
        )
    )
    null_count = result.scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"Backfill failed: {null_count} order(s) still have NULL price_list_id. "
            "All legacy orders must have at least one order line with a valid "
            "price_history_id to derive the price list."
        )

    # Enforce NOT NULL.
    op.alter_column(
        "order",
        "price_list_id",
        nullable=False,
        schema=schema,
    )


def downgrade() -> None:
    schema = "erp"

    # Allow NULL again (revert NOT NULL constraint).
    op.alter_column(
        "order",
        "price_list_id",
        nullable=True,
        schema=schema,
    )

    # NOTE: We do NOT NULL-out the backfilled values in downgrade
    # because that would lose data.  The column remains populated
    # but nullable, which is the state before this migration ran.
