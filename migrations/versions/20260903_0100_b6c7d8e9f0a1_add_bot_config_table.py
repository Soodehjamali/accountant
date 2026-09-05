"""Add bot_config table for per-platform bot runtime settings.

One row per ``bot_platform_ref`` (unique ``bot_platform_id``) holding:
- ``enabled`` -- whether the bot process should run this platform,
- ``token_ciphertext`` -- Fernet-encrypted bot token (never the raw token),
- ``token_hint`` -- last 4 chars of the token for admin display,
- ``runtime_status`` / ``last_heartbeat`` -- status reported by the actual
  bot process (heartbeat), so the admin UI never fakes "running".

Revision ID: b6c7d8e9f0a1
Revises: a9b8c7d6e5f4
Create Date: 2026-09-03 01:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "a9b8c7d6e5f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"
    op.create_table(
        "bot_config",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bot_platform_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.Column("token_hint", sa.String(40), nullable=True),
        sa.Column("runtime_status", sa.String(40), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        # UAC audit columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id", name="pk_bot_config"),
        sa.ForeignKeyConstraint(
            ["bot_platform_id"],
            [f"{schema}.bot_platform_ref.id"],
            name="fk_bot_config_bot_platform_id_bot_platform_ref_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{schema}.app_user.id"],
            name="fk_bot_config_created_by_app_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            [f"{schema}.app_user.id"],
            name="fk_bot_config_updated_by_app_user_id",
        ),
        sa.UniqueConstraint(
            "bot_platform_id",
            name="uq_bot_config_bot_platform_id",
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = "erp"
    op.drop_table("bot_config", schema=schema)