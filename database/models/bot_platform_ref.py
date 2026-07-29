from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import code_short_type


class BotPlatformRef(Base, UniversalAuditColumns):
    """R12 — bot_platform_ref: runtime-extensible bot platform catalog.

    ERD (06_ERD.md, PART B):
        R12 — bot_platform_ref
        Purpose: Runtime-extensible bot platforms (future WhatsApp/Signal).
        PK: id | code unique (TELEGRAM, BALE, WEBCHAT)
        Classification: R

    No "Important fields" line in the ERD -> field surface is id + code
    only, same minimal shape as notification_type_ref / report_type_ref.

    code -> code_short_type() (VARCHAR(40)): current seed values
    TELEGRAM=8, BALE=4, WEBCHAT=7 chars; future WHATSAPP=8, SIGNAL=6 --
    all fit comfortably within the 40-char budget.

    No CHECK constraint: the ERD's own "Runtime-extensible ... future
    WhatsApp/Signal" wording means the value set is explicitly NOT
    closed, so constraining code to the current three values would
    contradict the ERD's stated design intent.
    """

    __tablename__ = "bot_platform_ref"
    __mapper_args__ = {"version_id_col": "version"}

    id: GuidPk = id_column()
    code: Mapped[str] = mapped_column(
        code_short_type(), nullable=False, unique=True
    )


__all__ = ["BotPlatformRef"]