"""Add customer_price_list junction table (BR-P1).

Implements the first level of the SRS BR-P1 priority chain:
customer-specific > rep-tier > product default.

This junction table links customers to their assigned price lists
with time-window validity and priority ranking, following the same
pattern as customer_rep_assignment (C6).

Revision ID: c1d2e3f4a5b7
Revises: f5a6b7c8d9e0
Create Date: 2026-08-29 14:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b7"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"

    op.create_table(
        "customer_price_list",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "customer_id",
            sa.Uuid(),
            sa.ForeignKey(
                "erp.customer.id",
                name="fk_customer_price_list_customer_id_customer_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "price_list_id",
            sa.Uuid(),
            sa.ForeignKey(
                "erp.price_list.id",
                name="fk_customer_price_list_price_list_id_price_list_id",
            ),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        # UniversalAuditColumns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_customer_price_list"),
        schema=schema,
    )

    op.create_index(
        "idx_customer_price_list_customer_id",
        "customer_price_list",
        ["customer_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = "erp"

    op.drop_index(
        "idx_customer_price_list_customer_id",
        table_name="customer_price_list",
        schema=schema,
    )
    op.drop_table("customer_price_list", schema=schema)
