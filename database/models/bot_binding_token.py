"""Persistent binding token for bot identity binding (hardened Phase B design).

Replaces the Phase-A in-memory ``_binding_tokens`` dict with a database-backed
single-use, short-lived token table.

Design:
    * Tokens are generated via ``secrets.token_urlsafe(32)`` (43 chars,
      cryptographically secure).
    * Only the **SHA-256 hex digest** (64 chars) is persisted — the raw token
      is returned to the admin exactly once and never stored.
    * Each token is valid for 30 minutes (``_BINDING_TOKEN_TTL`` in the
      service layer) and may be consumed exactly once.
    * On consumption the row is stamped with ``consumed_at`` / ``consumed_by``;
      expired or already-consumed tokens are rejected at the service layer.
    * The token associates a ``Representative`` with a ``BotPlatformRef``
      (same contract as the Phase-A in-memory dict).

Naming convention:
    * ``token_hash`` — ``CHAR(64)`` (SHA-256 hex digest), same pattern as
      ``inventory_transaction.row_hash`` / ``attachment.checksum``.
    * FKs follow standard ``fk_index_name`` convention.
    * No CHECK constraints — no vocabulary column on this table.
    * Classification: M (mutable lifecycle record, not soft-deletable).

Out of scope:
    * Alembic migration (in a separate migration file).
    * TTL / cleanup policy (a scheduled job would purge old rows; not in
      this model's scope).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CHAR, DateTime, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.constants import HASH_HEX_LENGTH
from database.mixins import UniversalAuditColumns
from database.naming import fk_index_name


class BotBindingToken(Base, UniversalAuditColumns):
    """Persistent binding token for bot identity binding (Classification: M)."""

    __tablename__ = "bot_binding_token"

    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------- token_hash
    # SHA-256 hex digest of the raw token (never store the raw token).
    # CHAR(64) — same pattern as inventory_transaction.row_hash /
    # attachment.checksum (see module docstring).
    token_hash: Mapped[str] = mapped_column(
        CHAR(HASH_HEX_LENGTH),
        nullable=False,
        unique=True,
    )

    # --------------------------------------------------------- representative_id
    representative_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("bot_binding_token", "representative_id", "representative"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- bot_platform_id
    bot_platform_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "bot_platform_ref.id",
            name=fk_index_name("bot_binding_token", "bot_platform_id", "bot_platform_ref"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------------- expires_at
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ------------------------------------------------------------- consumed_at
    # NULL = not yet consumed; set on first (and only) consumption.
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------- consumed_by
    # FK to app_user.id — who consumed this token (the admin linking the
    # session). NULL = not yet consumed.
    consumed_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
    )


__all__ = ["BotBindingToken"]
