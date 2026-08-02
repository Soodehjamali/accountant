"""``M10 — app_user`` ORM model (system auth account linked to staff/rep).

Authority: ``06_ERD.md``, PART C → ``M10 — app_user``::

    M10 — app_user
    Purpose: System auth account linked to staff/rep.
    PK: id
    FK: representative_id → representative (nullable; staff users have none)
    Important fields: username/email (unique), password_hash, status,
                      last_login_at
    Unique: username (or email)
    Classification: M + soft-deletable

Same gap as every other M-table so far: ``06_ERD.md`` is M10's sole
authority — M10 has no detailed section in ``07_DATABASE_SPEC.md``.

``representative_id`` — ``ForeignKey()`` retrofitted now that M6 exists:
    ``representative_id`` was originally a plain ``UUID`` column with no
    ``ForeignKey()`` because ``representative`` (M6) did not exist in the
    codebase yet — the same deferred-FK deviation used at the time for
    ``warehouse.responsible_user_id`` and ``inventory_transaction.lot_id``.
    Now that ``database/models/representative.py`` has landed, this column
    carries a real ``ForeignKey("representative.id", ...)``, named via
    ``fk_index_name`` → ``fk_app_user_representative_id_representative``.
    ``nullable=True`` is unchanged: the ERD's own parenthetical — "staff
    users have none" — still applies; the column simply now enforces
    referential integrity for the rows that do populate it.

``username`` — column-type choice:
    ``code_short_type()`` (``VARCHAR(40)``) is used rather than a wider
    factory. The ERD gives no explicit length, but a login username is a
    short, controlled identifier in the same family as ``product.sku`` /
    ``carrier.code`` / ``warehouse.code`` — all of which already use this
    factory — rather than free-form display text like ``name_type()``
    (``VARCHAR(160)``, used for human display names) or an even wider field.
    40 characters comfortably covers conventional username schemes (e.g.
    ``firstname.lastname``, short handles) without over-provisioning.

``email`` — column-type choice AND uniqueness-reading ASSUMPTION:
    The ERD's ``Unique: username (or email)`` phrasing is ambiguous. It could
    mean either (a) both ``username`` and ``email`` are independently unique
    columns and either one alone can serve as the login identifier, or (b)
    there is a single logical "unique identifier" slot that is satisfied by
    whichever of the two is populated (implying at least one of them could be
    nullable). This is stated here explicitly as an assumption, not resolved
    silently:

    This model takes reading (a) — **both** ``username`` and ``email`` are
    implemented as independently unique, ``NOT NULL`` columns. Reasoning: the
    ERD lists ``password_hash`` as a sibling "important field" with no
    conditional/optional framing, and nothing in the ERD text marks either
    ``username`` or ``email`` as optional; if ``email`` is present on the
    entity at all, treating it as consistently populated (mirroring
    ``username``'s own always-populated, always-unique shape) is the safer
    reading than silently making one of the two nullable, which would be an
    unstated schema softening of an already-ambiguous ERD line. The
    alternative reading (email nullable-and-unique, for a user who only ever
    has a username) is equally defensible and is flagged here as an open
    question for the business/product owner, not resolved unilaterally.

    No dedicated email-specific column-type helper exists in
    ``database.types``. ``description_type()`` (``VARCHAR(255)``) is used as
    the closest existing factory — the same "closest existing factory, not a
    considered fit" treatment ``warehouse.py`` gives ``address`` — because
    RFC 5321's maximum email length (254 characters) fits within 255, unlike
    the narrower ``token_type()`` (``VARCHAR(120)``) or ``name_type()``
    (``VARCHAR(160)``) factories.

``password_hash`` — placeholder column-type choice:
    No dedicated password-hash type exists in ``database/types.py``.
    ``token_type()`` (``VARCHAR(120)``) is used as the closest existing
    factory — bcrypt and argon2 hash encodings are typically well under 120
    characters — flagged here as a placeholder choice, the same treatment
    ``warehouse.py`` gives ``address``'s use of ``description_type()``: not a
    considered hash-storage design (no algorithm-specific width reasoning),
    just the nearest existing fit.

``status`` — ASSUMPTION, not a fact taken from the ERD:
    PART A does **not** list a dedicated enum vocabulary for
    ``app_user.status`` — unlike ``representative``'s own explicit
    ``ACTIVE`` / ``SUSPENDED`` / ``OFFBOARDED`` vocabulary. This model
    assumes ``ACTIVE`` / ``INACTIVE``, mirroring ``carrier.py`` /
    ``warehouse.py``'s identical treatment of their own unspecified
    ``status`` fields. This is an assumption made to fill an unspecified
    vocabulary, not a value taken from the ERD text itself.

``last_login_at``:
    ``DateTime(timezone=True)``, nullable — a freshly created account has
    never logged in yet.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``AppUser`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), exactly like ``Currency`` / ``Product`` / ``Carrier`` /
    ``Warehouse``.

Soft delete:
    Classification is "M + soft-deletable" — same as ``product`` (M1) and
    ``warehouse`` (M4). Following their precedent, no reusable soft-delete
    mixin is relied on; ``deleted_at`` is declared directly as a nullable
    timezone-aware ``TIMESTAMPTZ``, default ``NULL`` meaning not deleted.

Naming convention:
    ``username`` and ``email`` both use column-level ``unique=True`` →
    ``uq_app_user_username`` / ``uq_app_user_email``, mirroring
    ``warehouse.code`` / ``product.sku``. The ``status`` vocabulary is
    bounded by a CHECK named via ``ck_index_name`` →
    ``ck_app_user_status_values``, mirroring ``carrier.py`` /
    ``warehouse.py``'s identical pattern.

Column-type choices:

* ``username`` — ``code_short_type()`` → ``VARCHAR(40)`` (see note above).
* ``email`` — ``description_type()`` → ``VARCHAR(255)`` (see note above).
* ``password_hash`` — ``token_type()`` → ``VARCHAR(120)`` (see note above).
* ``status`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``ACTIVE`` / ``INACTIVE`` (assumption — see note above).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import code_short_type, description_type, state_token_type, token_type


class AppUser(Base, UniversalAuditColumns):
    """``M10 — app_user`` — system auth account linked to staff/rep (Classification: M + soft-deletable)."""

    __tablename__ = "app_user"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------ representative_id
    # Retrofitted to a real ForeignKey now that representative (M6) exists
    # (see module docstring). Nullable, per the ERD: "staff users have none".
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "representative.id",
            name=fk_index_name("app_user", "representative_id", "representative"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- username
    username: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # ----------------------------------------------------------------- email
    # ASSUMPTION: independently unique, NOT NULL (reading (a) of the ERD's
    # ambiguous "Unique: username (or email)" phrasing — see module
    # docstring).
    email: Mapped[str] = mapped_column(
        description_type(),
        nullable=False,
        unique=True,
    )

    # ----------------------------------------------------------- password_hash
    # Placeholder column-type choice — see module docstring.
    password_hash: Mapped[str] = mapped_column(
        token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- status
    # ASSUMPTION: no dedicated vocabulary is given in PART A for
    # app_user.status. Mirrors carrier.py/warehouse.py's status pattern:
    # ACTIVE/INACTIVE.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------- last_login_at
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker (same pattern as product.py /
    # warehouse.py); NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # CHECK: status vocabulary — ASSUMPTION (see module docstring).
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name=ck_index_name("app_user", "status_values"),
        ),
    )


__all__ = ["AppUser"]
