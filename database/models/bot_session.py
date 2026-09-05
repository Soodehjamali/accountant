"""``M12 — bot_session`` ORM model (bind a messenger-platform user to a representative identity).

Authority: ``06_ERD.md``, line 34 → ``M12 — bot_session``::

    M12 — bot_session
    Purpose: Bind a messenger platform user → representative identity
             (SRS E30).
    PK: id
    FK: representative_id → representative, bot_platform_id →
        bot_platform_ref
    Important fields: platform_user_id, linked_at, status
        (LINKED/REVOKED/EXPIRED), session_token
    Unique: (bot_platform_id, platform_user_id)
    Business constraints: one platform user ↔ one representative;
        commands scoped by this binding; no cross-rep access
    Classification: M

Same gap as every other table with no dedicated spec section so far
(``product_serial.py`` (M3), ``discount.py`` (H3), ``price_list.py``
(C3), etc.): ``06_ERD.md`` is ``bot_session``'s sole authority --
``bot_session`` itself has no detailed section in ``07_DATABASE_SPEC.md``
(its own sibling ``H5 -- bot_message_log`` *does* have a full
``07_DATABASE_SPEC.md`` section -- confirmed by search, and that section's
own FK line -- *"bot_session_id -> bot_session.id"* -- corroborates this
table's own PK shape from the consumer side, but is not itself a
``bot_session`` section). ``06_ERD.md``'s own Aggregate Roots section
(line 191) additionally confirms ``bot_session`` is owned by the
``Representative`` aggregate root, alongside ``representative_contact`` /
``commission_config``.

Both FKs are real from the outset:
    ``representative`` and ``bot_platform_ref`` both already exist in this
    codebase, so ``representative_id`` and ``bot_platform_id`` are declared
    as real ``ForeignKey()`` constraints from the start. Neither is marked
    nullable in the ERD's ``FK:`` line, so both are declared ``NOT NULL`` --
    a session with no bound representative or no platform is meaningless.

``platform_user_id`` -- type choice:
    ``code_short_type()`` (``VARCHAR(40)``) -- the messenger platform's own
    user identifier (e.g. a Telegram numeric user ID or a Bale user
    identifier), transcribed as a string regardless of the source
    platform's own native ID shape. The same "short business-facing
    identifier" factory already used for ``warehouse.code`` /
    ``product_serial.serial_number`` fits here for the same reason:
    platform user identifiers are short, opaque tokens from this system's
    point of view, not free text. ``NOT NULL`` -- the whole point of this
    row is binding a specific platform identity.

``linked_at`` -- ``NOT NULL DEFAULT now()``, per explicit instruction:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` -- the same
    ``now()``-defaulted-timestamp treatment already given to
    ``order.ordered_at`` / ``order_status_history.event_at`` /
    ``user_role.assigned_at``.

``status`` -- explicit ERD vocabulary, three-member CHECK, same treatment as
``product_serial.status``:
    Bounded to exactly ``LINKED`` / ``REVOKED`` / ``EXPIRED`` per the ERD's
    own parenthetical. ``state_token_type()`` (``VARCHAR(16)``) fits every
    member (``REVOKED``/``EXPIRED`` are the longest at 7 characters), with
    a matching CHECK named via ``ck_index_name``. **No default** is given --
    the ERD's inline text lists the vocabulary but states no ``DEFAULT``,
    the same restraint already applied to ``product_serial.status`` /
    ``customer_rep_assignment.priority`` (both left without a fabricated
    default because their own ERD entries gave none); ``status`` is
    declared ``NOT NULL`` with no default, the application always supplies
    the initial value.

``session_token`` -- ``token_type()``, an exact-purpose factory, not a
placeholder-width judgment call:
    ``database/types.py`` already has ``token_type()`` (``VARCHAR(120)``),
    documented on that factory itself as being for *"session tokens,
    tracking-adjacent tokens, ..."* -- an exact, purpose-built match for
    this column, not a "closest existing factory" placeholder decision the
    way e.g. ``representative_contact.value`` had to reach for
    ``description_type()``. (Note: the conceptual reference point this
    task named for the pattern, ``system_config.value``, has not been
    built in this codebase yet as of this change -- the same situation
    already noted on ``product_serial.py`` for ``system_config.key`` --
    but ``token_type()`` itself already exists in ``database/types.py``
    independent of whether ``system_config`` has been built, so it is used
    directly here regardless.) ``NOT NULL`` -- no nullable annotation
    given, and a session with no token is not a usable session.

Unique constraint -- literal ERD column pair, ordinary composite case (NOT
a naming trap):
    ``UniqueConstraint("bot_platform_id", "platform_user_id")`` via
    ``uq_index_name`` + ``composite_descriptor`` -- the ERD gives this
    constraint's columns explicitly, so the standard helper output is used
    as-is with no override, the same ordinary treatment
    ``warehouse_assignment`` / ``price_history``'s own literal composite
    uniqueness already received.

Business constraints -- one is already implied by the schema, two are
deliberately NOT expressible/added at the database level:
    All three deserve separate treatment rather than one blanket
    "out of scope" note, because they are not all the same kind of rule:

    1. *"One platform user <-> one representative"* -- this is **already
       fully guaranteed** by the literal ``UniqueConstraint`` above, with
       no additional schema machinery needed. Because
       ``(bot_platform_id, platform_user_id)`` is unique across the
       *entire* table (not filtered/partial), at most one row can ever
       exist for a given platform identity -- and since that one row
       carries exactly one ``representative_id``, "one platform user maps
       to at most one representative" falls directly out of the ordinary
       composite uniqueness the ERD already specifies. Re-linking a
       platform identity to a different representative later is modeled
       as an ``UPDATE`` to that same existing row (new
       ``representative_id``/``status``/``session_token``/``linked_at``),
       not a new row -- consistent with this table's plain ``M``
       (mutable, not append-only) classification. No separate constraint
       is needed to state a rule the schema already guarantees as a side
       effect of the constraint the ERD asked for directly.
    2. *"Commands scoped by this binding"* and 3. *"no cross-rep access"*
       -- unlike rule 1, these are not data-integrity facts about which
       rows may coexist in this table at all; they describe *how the bot
       command-processing service must behave at request time* (look up
       the ``bot_session`` for an incoming platform message by
       ``(bot_platform_id, platform_user_id)``, then scope everything that
       message is allowed to do to that session's own
       ``representative_id``, rejecting any attempt to reach another
       representative's data). There is no row, column, or constraint this
       table could add to encode "requests must be scoped this way" --
       the table already supplies the one durable fact
       (which representative a platform identity is currently bound to)
       that this authorization logic depends on; everything past that is
       what the service does *with* that fact on each incoming command,
       the same category of business constraint already left to the
       service/application layer for ``order``'s own state-machine
       transition graph and ``customer_rep_assignment``'s
       overlap-prevention rule. No ``CHECK``/``EXCLUDE``/trigger of any
       kind is added for either of these two.

No ``deleted_at`` -- this table is plain ``M``, with no soft-delete
qualifier:
    Same reasoning as ``product_serial.py``: several of this table's
    domain neighbors (``representative.py`` itself, if soft-deletable;
    ``warehouse_location.py`` / the contact tables) are ``M +
    soft-deletable`` and declare their own ``deleted_at`` -- but
    ``bot_session``'s own ERD classification is bare ``"M"``, so no
    ``deleted_at`` column is declared here either. This table's own
    lifecycle is instead fully expressed through its ``status`` enum
    (``LINKED`` -> ``REVOKED`` / ``EXPIRED``), the identical role
    ``status`` already plays on ``product_serial.py``.

Audit-column family -- ``UniversalAuditColumns`` (UAC), per instruction:
    Plain ``M`` (master/reference data about an active binding)
    classification -- an ordinary mutable record whose own ``status``
    (and, per point 1 above, potentially its own ``representative_id`` on
    re-link) changes over its lifetime, the same reasoning already
    established for ``product_serial.py`` and every other UAC-using
    ``M``-classified table in this codebase. ``BotSession`` opts its
    ``version`` column into SQLAlchemy optimistic locking
    (``__mapper_args__ = {"version_id_col": "version"}``), consistent with
    every other UAC-using model in this codebase.

Naming convention:
    Both FKs use ``fk_index_name`` normally
    (``fk_bot_session_representative_id_representative_id``,
    ``fk_bot_session_bot_platform_id_bot_platform_ref_id``). The unique
    constraint uses ``uq_index_name`` + ``composite_descriptor`` as an
    ordinary composite case -> ``uq_bot_session_bot_platform_id_platform_
    user_id``. The ``status`` vocabulary CHECK uses ``ck_index_name``
    normally -> ``ck_bot_session_status_values``.

Column-type choices:

* ``platform_user_id`` -- ``code_short_type()`` -> ``VARCHAR(40)``.
* ``linked_at`` -- ``DateTime(timezone=True)``, ``NOT NULL DEFAULT
  now()``.
* ``status`` -- ``state_token_type()`` -> ``VARCHAR(16)``, no default (see
  dedicated note above).
* ``session_token`` -- ``token_type()`` -> ``VARCHAR(120)`` (see dedicated
  note above).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import code_short_type, state_token_type, token_type


class BotSession(Base, UniversalAuditColumns):
    """``M12 — bot_session`` — bind a messenger platform user to a representative identity (Classification: M)."""

    __tablename__ = "bot_session"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # --------------------------------------------------------- representative_id
    representative_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("bot_session", "representative_id", "representative"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- bot_platform_id
    bot_platform_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "bot_platform_ref.id",
            name=fk_index_name("bot_session", "bot_platform_id", "bot_platform_ref"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ platform_user_id
    platform_user_id: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- linked_at
    linked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # -------------------------------------------------------------------- status
    # Explicit ERD vocabulary: LINKED/REVOKED/EXPIRED. No default given in
    # the ERD -- see module docstring's dedicated section.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- session_token
    # token_type() -- exact-purpose factory (session tokens), not a
    # placeholder. See module docstring's dedicated section.
    session_token: Mapped[str] = mapped_column(
        token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------- last_seen
    # When the platform identity last used this session (any authenticated
    # API call). Updated by the bot-auth dependency on every request.
    # Nullable: a freshly bound session may have no traffic yet.
    last_seen: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------------- expires_at
    # Optional absolute expiry for the binding. When set and in the past,
    # the session is treated as EXPIRED by the auth layer (the status column
    # keeps the LINKED/REVOKED/EXPIRED vocabulary -- this column simply
    # lets time-based expiry be enforced without rewriting status).
    # Nullable: a session with no expiry stays LINKED until revoked.
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- ordinary composite case, literal ERD column pair. Also
        # already implies "one platform user <-> one representative" -- see
        # module docstring's "Business constraints" section.
        UniqueConstraint(
            "bot_platform_id",
            "platform_user_id",
            name=uq_index_name(
                "bot_session",
                composite_descriptor(("bot_platform_id", "platform_user_id")),
            ),
        ),
        # CHECK: status vocabulary -- the only CHECK on this table.
        CheckConstraint(
            "status IN ('LINKED', 'REVOKED', 'EXPIRED')",
            name=ck_index_name("bot_session", "status_values"),
        ),
    )


__all__ = ["BotSession"]
