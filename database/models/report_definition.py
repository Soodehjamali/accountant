"""``M17 — report_definition`` ORM model (saved/scheduled report configuration).

Authority: ``06_ERD.md``, line 122 → ``M17 — report_definition``::

    M17 — report_definition
    Purpose: Saved/scheduled report configuration (SRS E34).
    PK: id
    FK: report_type_id → report_type_ref, owner_user_id → app_user
    Important fields: name, parameters (JSONB), schedule_cron (nullable),
                      output_format, is_active
    Unique: (owner_user_id, name)
    Classification: M + soft-deletable

``06_ERD.md`` is M17's sole authority: like every other M-table so far
(``product.py`` (M1), ``attachment.py`` (M14), ``product_image.py`` (M15)),
M17 has no detailed section in ``07_DATABASE_SPEC.md``.

``report_type_id`` — ``ForeignKey("report_type_ref.id")``, ``NOT NULL``:
    ``report_type_ref`` (R10 — "Registry of report kinds") already exists
    in this codebase. The ERD's ``FK:`` line gives no nullable annotation
    for either FK on this table, and a saved report configuration with no
    report kind is meaningless, so both FKs are declared ``NOT NULL``.

``owner_user_id`` — ``ForeignKey("app_user.id")``, ``NOT NULL``:
    ``app_user`` already exists in this codebase. Ordinary, single-target
    FK — every saved report belongs to exactly one owning user.

``name`` — ``name_type()`` -> ``VARCHAR(160)``:
    Per direct instruction. This codebase's factory for "human display
    name" columns (``product.name``/``warehouse.name``/full names, per its
    own docstring) — the closest existing semantic fit for a
    user-assigned, human-readable saved-report name. Declared ``NOT NULL``
    — the ERD's own ``Unique: (owner_user_id, name)`` line requires every
    row to carry a real name to participate in that constraint at all.

``parameters`` — first JSONB column in this codebase, deliberately raw
``postgresql.JSONB``, NOT a new ``database/types.py`` factory:
    Per direct instruction. Every existing ``database/types.py`` factory
    maps to a ``NumericPrecision``/``StringLength`` constant sourced from
    repeated ``NUMERIC(p,s)``/``VARCHAR(N)`` shapes already appearing
    across ``07_DATABASE_SPEC.md`` (per ``types.py``'s own module
    docstring) — there is no existing ``JSON``/``JSONB`` precedent
    anywhere in this codebase to generalize from, and inventing a
    ``json_type()`` factory today, on the strength of exactly one
    call site, would be speculative infrastructure with no second
    consumer to validate the abstraction against (contrast
    ``money_type()``/``cost_type()``/``rate_type()``, each already used by
    multiple columns across multiple tables before this task). This model
    therefore imports ``sqlalchemy.dialects.postgresql.JSONB`` directly,
    the same "consume the concrete SQLAlchemy/dialect type directly when no
    factory abstraction yet exists for it" treatment already given to
    ``HASH_HEX_LENGTH`` in ``inventory_transaction.py`` /
    ``attachment.py`` (a plain ``sqlalchemy.CHAR(HASH_HEX_LENGTH)`` import
    rather than a speculative factory). ``postgresql.JSONB`` specifically
    (rather than the cross-dialect ``sqlalchemy.JSON``) is chosen because
    this schema already commits to PostgreSQL-specific constructs
    elsewhere unconditionally (e.g. every ``postgresql_where``-filtered
    partial unique index already built in this codebase --
    ``costing_method_config``/``warehouse_assignment``/``product_image`` --
    has no cross-dialect fallback either), and ``JSONB`` is PostgreSQL's
    binary-indexed, query-optimized JSON storage format for exactly the
    "arbitrary structured report-parameter payload, may later be queried
    or indexed" role this column plays. Declared ``NOT NULL`` with no
    default: the ERD gives no default value to transcribe, and a bare
    ``{}`` default would be a schema opinion about "no parameters" with no
    textual basis — a report definition genuinely without parameters can
    still supply an explicit empty JSON object at the application layer, so
    this column requires that decision to be made explicitly by the caller
    rather than silently defaulted.

``schedule_cron`` — ``cron_expression_type()`` -> ``VARCHAR(60)``, nullable
per direct ERD annotation:
    This factory's own docstring names ``report_definition.schedule_cron``
    by exact column name as its motivating use case ("Maps to
    ``StringLength.CRON_EXPRESSION`` (``report_definition.schedule_cron``)")
    — a direct, spec-literal match, not an inference. Nullable per the
    ERD's own explicit ``(nullable)`` annotation and per direct
    instruction: a report definition may be on-demand-only, with no
    recurring schedule at all.

``output_format`` — free token, deliberately NO CHECK vocabulary:
    Per direct instruction. This column resembles
    ``generated_document.format`` (``PDF``/``CSV``/``XLSX``, per that
    table's own ERD line) closely enough to *look* like a candidate for
    the same CHECK-bounded-vocabulary treatment already given to
    ``discount.discount_type`` / ``price_list.price_type`` /
    ``costing_method_config.method`` / ``product_serial.status`` — but the
    M17 ERD line itself gives ``output_format`` no enumerated value set of
    its own, unlike every one of those columns, each of which the ERD (or
    an adjacent line naming the exact same column, as with M16's
    ``format``) spells out explicitly. Borrowing M16's ``PDF/CSV/XLSX``
    vocabulary for M17's own, differently-named ``output_format`` column
    would be filling this table's gap with a *different* table's stated
    values — a textual basis that does not actually belong to this column
    — the same "don't fabricate schema/vocabulary with no textual basis
    for *this* column" discipline already applied to
    ``system_config.category`` (open label, no ``PART A`` enum) and, more
    directly, ``attachment.entity_type`` (an adjacent table's own
    ERD-adjacent example list was explicitly not treated as this column's
    vocabulary either). ``output_format`` is therefore modeled as
    ``state_token_type()`` -> ``VARCHAR(16)`` — a short classification
    token, matching the width class already used for comparable
    short-token columns in this codebase (``method``/``category``/
    ``status``), chosen over ``type_token_type()``'s wider 40-char
    "polymorphic discriminator" framing because ``output_format`` is a
    closed-in-spirit-but-textually-unstated short format code, closer in
    shape to ``method``/``category`` than to a growing polymorphic
    discriminator — with **no** ``CheckConstraint`` bounding its values,
    the same "app/validation may enforce a vocabulary the schema does not
    state" treatment already given to ``system_config.category`` and
    ``attachment.entity_type``. Declared ``NOT NULL`` — the ERD names it
    among ``Important fields:`` alongside ``name``/``schedule_cron``/
    ``is_active`` with no nullable annotation of its own (contrast
    ``schedule_cron``'s explicit ``(nullable)``), so a report definition is
    expected to always specify some output format even without the schema
    constraining which ones are legal.

``is_active`` — boolean flag, default ``true``, same idiom as
``price_list.is_active``:
    Per direct instruction. Identical shape to ``price_list.is_active``:
    ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT true`` — a bare ``INSERT``
    with no explicit value yields a valid, well-defined "active" row, the
    same reasoning ``price_list.is_active``'s own docstring already gives.
    A deactivated report definition is simply ``is_active = false`` rather
    than deleted or unschedulable through some other mechanism — this
    column, not ``deleted_at``, is this table's primary on/off lifecycle
    switch, mirroring ``price_list``'s own "``is_active`` already gives
    this table its own on/off lifecycle flag" reasoning.

Uniqueness — ordinary composite case, literal ERD column pair:
    ``UniqueConstraint(owner_user_id, name)`` via ``uq_index_name`` +
    ``composite_descriptor`` — an owner may not save two report
    definitions under the same name, but two different owners may each
    have their own report named identically. No conditional/partial
    uniqueness rule is stated on this table (unlike
    ``warehouse_assignment``/``product_image``'s own paired
    ``is_primary``-conditional rule) — the ERD's ``Unique:`` line here
    names exactly one plain composite tuple, so exactly one
    ``UniqueConstraint`` is declared.

No CHECK constraints:
    Both FKs carry no additional business rule stated in the ERD line
    itself; ``parameters`` is unstructured JSONB; ``schedule_cron`` is free
    text; ``output_format`` is deliberately left an open token (see
    dedicated section above); ``is_active`` is a plain boolean. No
    vocabulary column exists on this table to CHECK-bound.

Soft delete — ``deleted_at``, per direct instruction:
    Per the ERD's own ``Classification: M + soft-deletable``. ``deleted_at``
    is declared directly, the same nullable-timezone-aware-timestamp
    pattern ``product.py`` (M1) / ``attachment.py`` (M14) /
    ``product_image.py`` (M15) already establish for this codebase's
    soft-delete columns: ``NULL`` denotes a report definition that has not
    been (soft-)deleted. Note this is independent of ``is_active``: a
    report can be deactivated (``is_active = false``) without being
    deleted, and a soft-deleted row is removed from normal visibility
    regardless of its ``is_active`` value.

Audit-column family — ``UniversalAuditColumns`` (UAC), per instruction:
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``ReportDefinition`` uses UAC and opts its ``version``
    column into SQLAlchemy optimistic locking (``__mapper_args__ =
    {"version_id_col": "version"}``), matching every other UAC-using model
    in this codebase.

Naming convention:
    Both FKs use ``fk_index_name`` normally —
    ``fk_report_definition_report_type_id_report_type_ref_id`` /
    ``fk_report_definition_owner_user_id_app_user_id``. The composite
    ``UniqueConstraint`` uses ``uq_index_name`` + ``composite_descriptor``
    -> ``uq_report_definition_owner_user_id_name``. No CHECK/``Index`` of
    any other kind exists on this table.

Column-type choices:

* ``name`` — ``name_type()`` -> ``VARCHAR(160)``.
* ``parameters`` — raw ``postgresql.JSONB`` (see dedicated section above;
  first JSONB column in this codebase).
* ``schedule_cron`` — ``cron_expression_type()`` -> ``VARCHAR(60)``,
  nullable.
* ``output_format`` — ``state_token_type()`` -> ``VARCHAR(16)``, no CHECK
  (see dedicated section above).
* ``is_active`` — ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT true``.
* ``deleted_at`` — ``DateTime(timezone=True)``, nullable.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import composite_descriptor, fk_index_name, uq_index_name
from database.types import cron_expression_type, name_type, state_token_type


class ReportDefinition(Base, UniversalAuditColumns):
    """``M17 — report_definition`` — saved/scheduled report configuration (Classification: M + soft-deletable)."""

    __tablename__ = "report_definition"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ report_type_id
    report_type_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "report_type_ref.id",
            name=fk_index_name("report_definition", "report_type_id", "report_type_ref"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- owner_user_id
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("report_definition", "owner_user_id", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------------- name
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- parameters
    # Raw postgresql.JSONB -- first JSONB column in this codebase, no
    # database/types.py factory exists yet. See module docstring.
    parameters: Mapped[dict] = mapped_column(
        JSONB(),
        nullable=False,
    )

    # ----------------------------------------------------------- schedule_cron
    # Nullable -- a report may be on-demand-only, per direct ERD
    # annotation. See module docstring.
    schedule_cron: Mapped[str | None] = mapped_column(
        cron_expression_type(),
        nullable=True,
    )

    # ------------------------------------------------------------ output_format
    # Free token, deliberately no CHECK vocabulary -- the ERD gives this
    # column no enumerated value set of its own. See module docstring.
    output_format: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- is_active
    is_active: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )

    # ------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker; NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "name",
            name=uq_index_name(
                "report_definition",
                composite_descriptor(("owner_user_id", "name")),
            ),
        ),
    )


__all__ = ["ReportDefinition"]
