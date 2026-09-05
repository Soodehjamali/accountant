"""Bot session expiry/last-seen columns + extended audit actions.

Bot session (ADR-013 REST bot architecture):
    * ``bot_session.last_seen``  -- when the platform identity last used the
      session (updated by the bot-auth dependency on every API call).
    * ``bot_session.expires_at`` -- optional absolute expiry; when set and in
      the past, the auth layer treats the session as EXPIRED without mutating
      the ``status`` column (LINKED/REVOKED/EXPIRED vocabulary is unchanged).

Audit log:
    The ``ck_audit_log_action`` CHECK is extended with three actions the bot
    flow records:
      * ``AUTHENTICATE`` -- phone-verification attempts/results.
      * ``QUERY``        -- representative-scoped bot data queries
                           (inventory / reports).
      * ``ATTEMPT``      -- bot write attempts (e.g. invoice creation) that
                           are recorded before the mutation is attempted.

Revision ID: a9b8c7d6e5f4
Revises: merge_heads
Create Date: 2026-09-03 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: str | None = "merge_heads"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    schema = "erp"

    # --- bot_session: last_seen + expires_at -------------------------------
    op.add_column(
        "bot_session",
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "bot_session",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )

    # --- audit_log: extend action vocabulary -------------------------------
    op.drop_constraint("ck_audit_log_action", "audit_log", schema=schema)
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('CREATE','UPDATE','DELETE','APPROVE','REJECT','OVERRIDE',"
        "'AUTHENTICATE','QUERY','ATTEMPT')",
        schema=schema,
    )


def downgrade() -> None:
    schema = "erp"

    op.drop_constraint("ck_audit_log_action", "audit_log", schema=schema)
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN ('CREATE','UPDATE','DELETE','APPROVE','REJECT','OVERRIDE')",
        schema=schema,
    )

    op.drop_column("bot_session", "expires_at", schema=schema)
    op.drop_column("bot_session", "last_seen", schema=schema)