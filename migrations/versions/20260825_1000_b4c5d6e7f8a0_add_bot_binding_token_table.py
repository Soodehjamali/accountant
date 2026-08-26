"""Add bot_binding_token table for persistent identity binding

Replaces the Phase-A in-memory binding-token dict with a database-backed
single-use, short-lived token table.  Tokens are stored as SHA-256 hashes;
the raw token is returned to the admin exactly once and never persisted.

Revision ID: b4c5d6e7f8a0
Revises: a1b2c3d4e5f6
Create Date: 2026-08-25 10:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a0'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = "erp"
    op.create_table(
        "bot_binding_token",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        sa.Column("representative_id", sa.Uuid(), nullable=False),
        sa.Column("bot_platform_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by", sa.Uuid(), nullable=True),
        # UAC audit columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id", name="pk_bot_binding_token"),
        sa.ForeignKeyConstraint(
            ["representative_id"],
            [f"{schema}.representative.id"],
            name="fk_bot_binding_token_representative_id_representative_id",
        ),
        sa.ForeignKeyConstraint(
            ["bot_platform_id"],
            [f"{schema}.bot_platform_ref.id"],
            name="fk_bot_binding_token_bot_platform_id_bot_platform_ref_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{schema}.app_user.id"],
            name="fk_bot_binding_token_created_by_app_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            [f"{schema}.app_user.id"],
            name="fk_bot_binding_token_updated_by_app_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by"],
            [f"{schema}.app_user.id"],
            name="fk_bot_binding_token_consumed_by_app_user_id",
        ),
        schema=schema,
    )
    op.create_unique_constraint(
        "uq_bot_binding_token_token_hash",
        "bot_binding_token",
        ["token_hash"],
        schema=schema,
    )
    op.create_index(
        "idx_bot_binding_token_expires_at",
        "bot_binding_token",
        ["expires_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = "erp"
    op.drop_index("idx_bot_binding_token_expires_at", schema=schema)
    op.drop_constraint("uq_bot_binding_token_token_hash", "bot_binding_token", schema=schema)
    op.drop_table("bot_binding_token", schema=schema)
