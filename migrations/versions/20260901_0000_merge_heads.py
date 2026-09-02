"""Merge price_list backfill and customer_price_list branches.

Both branches diverged from f5a6b7c8d9e0:
  - b1c2d3e4f5a6: Backfill order.price_list_id from order lines' price history.
  - c1d2e3f4a5b7: Add customer_price_list junction table (BR-P1).

Revision ID: merge_heads
Revises: b1c2d3e4f5a6, c1d2e3f4a5b7
Create Date: 2026-09-01 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "merge_heads"
down_revision: str | None = ("b1c2d3e4f5a6", "c1d2e3f4a5b7")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
