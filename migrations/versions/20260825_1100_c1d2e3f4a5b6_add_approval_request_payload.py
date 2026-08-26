"""Add payload column to approval_request for deferred command execution.

Per ADR-008 §6, approval_required=True commands store their execution
data in the approval_request row so that the mutation can be replayed
after approval.  The payload column stores the serialized command data
as JSON.

Revision ID: c1d2e3f4a5b6
Revises: b4c5d6e7f8a0
Create Date: 2026-08-25 11:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b4c5d6e7f8a0"
branch_labels: str | None = None
depends_on: str | None = None

schema = "erp"


def upgrade() -> None:
    op.add_column(
        "approval_request",
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=True,
        ),
        schema=schema,
    )


def downgrade() -> None:
    op.drop_column("approval_request", "payload", schema=schema)
