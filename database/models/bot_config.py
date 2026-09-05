"""``bot_config`` ORM model -- per-platform bot settings (tokens encrypted at rest).

Purpose:
    Stores the runtime configuration of each bot platform (Telegram, Bale):
    enabled flag, the bot token (encrypted at rest -- the raw token is
    never stored, only a Fernet ciphertext plus a display hint of the last
    4 characters), and the live runtime status reported by the bot process
    itself (heartbeat).

Design:
    * One row per ``bot_platform_ref`` (unique ``bot_platform_id``).
    * ``token_ciphertext`` -- Fernet-encrypted token (``cryptography``).
      The encryption key is derived from the application ``SECRET_KEY``
      (see ``services/bot_config_service.py``).  NULL when no token has
      been configured.
    * ``token_hint`` -- the last 4 characters of the token, for display
      in the admin UI without exposing the secret.
    * ``enabled`` -- whether the bot process should run this platform.
    * ``runtime_status`` -- ``RUNNING`` / ``STOPPED`` / ``ERROR`` as
      reported by the bot process heartbeat (NULL = never reported).
    * ``last_heartbeat`` -- when the bot process last reported in; the
      admin status view treats a stale heartbeat as STOPPED (status is
      never faked -- it always derives from a real process report).
    * ``bot_username`` / ``bot_name`` / ``bot_id`` -- the bot's identity
      as reported by the platform's ``getMe`` on a successful connection
      test.  Display-only fields; cleared whenever the token is replaced.

Classification: M (mutable configuration record) -- UAC.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql import text as sa_text

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import composite_descriptor, fk_index_name, uq_index_name
from database.types import code_short_type, name_type


class BotConfig(Base, UniversalAuditColumns):
    """``bot_config`` -- per-platform bot runtime settings (Classification: M)."""

    __tablename__ = "bot_config"

    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------- bot_platform_id
    bot_platform_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "bot_platform_ref.id",
            name=fk_index_name("bot_config", "bot_platform_id", "bot_platform_ref"),
        ),
        nullable=False,
    )

    # ----------------------------------------------------------------- enabled
    enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    # -------------------------------------------------------- token_ciphertext
    # Fernet-encrypted bot token (never the raw token).  Text -- Fernet
    # ciphertexts are variable length base64url strings.
    token_ciphertext: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    # ------------------------------------------------------------ token_hint
    # Last 4 characters of the plaintext token, for admin display only.
    token_hint: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )

    # --------------------------------------------------------- runtime_status
    # RUNNING / STOPPED / ERROR as reported by the bot process heartbeat.
    # NULL = the process has never reported a status for this platform.
    runtime_status: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )

    # -------------------------------------------------------- last_heartbeat
    last_heartbeat: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------ bot_username
    # Bot username (@handle) as reported by the platform's getMe on a
    # successful connection test.  Display-only; never a secret.
    bot_username: Mapped[str | None] = mapped_column(
        name_type(),
        nullable=True,
    )

    # --------------------------------------------------------------- bot_name
    # Bot display name (first_name) as reported by getMe.  Display-only.
    bot_name: Mapped[str | None] = mapped_column(
        name_type(),
        nullable=True,
    )

    # ---------------------------------------------------------------- bot_id
    # Platform bot user id as reported by getMe (string form).  Display-only.
    bot_id: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- one config row per platform.
        UniqueConstraint(
            "bot_platform_id",
            name=uq_index_name("bot_config", "bot_platform_id"),
        ),
    )


__all__ = ["BotConfig"]