"""Add approval_number column to approval_request.

Per the approval queue milestone, pending approval requests need a
human-readable reference (APR-XXXXXXXX) for Telegram bot UX.  Raw UUIDs
must never appear in Telegram responses.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-26 10:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | None = None
depends_on: str | None = None

schema = "erp"


def upgrade() -> None:
    op.add_column(
        "approval_request",
        sa.Column(
            "approval_number",
            sa.String(40),
            nullable=True,
        ),
        schema=schema,
    )
    op.create_index(
        "uq_approval_request_approval_number",
        "approval_request",
        ["approval_number"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("approval_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_approval_request_approval_number",
        table_name="approval_request",
        schema=schema,
    )
    op.drop_column("approval_request", "approval_number", schema=schema)
