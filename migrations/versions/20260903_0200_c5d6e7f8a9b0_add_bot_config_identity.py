"""Add bot identity columns (bot_username, bot_name, bot_id) to bot_config.

Populated from the platform's ``getMe`` response after a successful
connection test, so the admin UI can display the bot's real name/username
without asking the admin to type them and without ever exposing the token.

Revision ID: c5d6e7f8a9b0
Revises: b6c7d8e9f0a1
Create Date: 2026-09-03 02:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"
    op.add_column("bot_config", sa.Column("bot_username", sa.String(255), nullable=True), schema=schema)
    op.add_column("bot_config", sa.Column("bot_name", sa.String(255), nullable=True), schema=schema)
    op.add_column("bot_config", sa.Column("bot_id", sa.String(40), nullable=True), schema=schema)


def downgrade() -> None:
    schema = "erp"
    op.drop_column("bot_config", "bot_id", schema=schema)
    op.drop_column("bot_config", "bot_name", schema=schema)
    op.drop_column("bot_config", "bot_username", schema=schema)