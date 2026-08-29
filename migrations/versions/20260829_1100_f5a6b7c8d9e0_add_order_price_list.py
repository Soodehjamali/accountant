"""Add price_list_id FK to order table.

Integrates the price list system with orders so that each order
references a specific price list used to resolve product prices
for its lines.

Revision ID: f5a6b7c8d9e0
Revises: e3f4a5b6c7d8
Create Date: 2026-08-29 11:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"

    # Add price_list_id as a nullable column first, then backfill, then
    # make NOT NULL.  This avoids failures on tables that already have rows.
    op.add_column(
        "order",
        sa.Column(
            "price_list_id",
            sa.Uuid(),
            nullable=True,
        ),
        schema=schema,
    )

    op.create_foreign_key(
        "fk_order_price_list_id_price_list_id",
        "order",
        "price_list",
        ["price_list_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
    )

    op.create_index(
        "idx_order_price_list_id",
        "order",
        ["price_list_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = "erp"

    op.drop_index(
        "idx_order_price_list_id",
        table_name="order",
        schema=schema,
    )
    op.drop_constraint(
        "fk_order_price_list_id_price_list_id",
        "order",
        schema=schema,
    )
    op.drop_column(
        "order",
        "price_list_id",
        schema=schema,
    )
