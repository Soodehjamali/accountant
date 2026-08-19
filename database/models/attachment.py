"""``M14 — attachment`` ORM model (generic polymorphic file attachment).

Authority: ``06_ERD.md``, line 114 → ``M14 — attachment``::

    M14 — attachment
    Purpose: Generic polymorphic file attachment — supporting evidence for
             adjustments, returns, disputes, KYC docs, etc.
    PK: id
    FK: entity_type + entity_id (polymorphic), uploaded_by → app_user
    Important fields: file_name, mime_type, size_bytes, storage_key
                      (S3-compatible object key), checksum
    Unique: storage_key
    Business constraints: soft-deletable (deleted_at) — unlike ledger/
                          history tables, a wrongly-uploaded attachment
                          may legitimately be removed without breaking
                          financial integrity
    Classification: M + soft-deletable

``06_ERD.md`` is M14's sole authority: like every other M-table so far
(``product.py`` (M1), ``product_lot.py`` (M2), ``product_serial.py`` (M3)),
M14 has no detailed section in ``07_DATABASE_SPEC.md``.

Two further ERD lines, quoted verbatim because they directly shape the
polymorphic-column decision below::

    Line 146 (PART G/architecture note): "Polymorphic references
    (entity_type + entity_id) are used repeatedly: inventory_transaction
    .reference, customer_ledger_entry.reference, audit_log, approval_
    request, attachment, generated_document. A plain FK cannot enforce
    these at the DB layer — physical design must choose between (a)
    per-type nullable FK columns, (b) a supertype/subtype table, or (c)
    trigger-enforced polymorphic integrity. Left open by design since this
    is a logical ERD."

    Line 220 (PART M): "The polymorphic (entity_type, entity_id) pattern
    used across audit_log, approval_request, attachment, generated_
    document, and customer_ledger_entry.reference will not scale well as a
    single unindexed JSONB blob — each should carry its composite index
    (PART K) ..."

``entity_type`` / ``entity_id`` — polymorphic, permanently plain columns,
NOT a deferred-FK case:
    This is a categorically different situation from every prior
    plain-UUID-pending-a-table case in this codebase (e.g.
    ``app_user.representative_id`` before ``representative`` (M6) landed,
    documented in ``app_user.py``'s own docstring as a deferred FK later
    retrofitted to a real ``ForeignKey()`` once the target table existed).
    Those deferred FKs were *temporary*: the column always had exactly
    *one* intended target table, and the plain-``UUID`` declaration was
    only a placeholder for a target that had not yet been built in this
    codebase — as soon as the target landed, the column was retrofitted to
    a real ``ForeignKey()``.

    ``attachment.entity_id`` has no such single target, ever, by design:
    per the ERD's own Purpose line, an attachment can evidence "adjustments,
    returns, disputes, KYC docs, etc." — a genuinely open set of possible
    parent entities selected *at the row level* by whatever value
    ``entity_type`` holds on that particular row. A single SQL
    ``ForeignKey`` constraint binds a column to exactly one target table at
    DDL-definition time; it has no mechanism to point row 1 at
    ``stock_adjustment.id``, row 2 at ``dispute.id``, and row 3 at some
    ``kyc_document.id`` depending on a *sibling column's* value. This is
    precisely the ambiguity line 146 names directly:
    "A plain FK cannot enforce these at the DB layer" — followed by three
    named alternative physical-design strategies ((a) per-type nullable FK
    columns, (b) a supertype/subtype table, (c) trigger-enforced polymorphic
    integrity), none of which the ERD instructs this table to adopt, closing
    with "Left open by design since this is a logical ERD."

    So, unlike a deferred FK, there is no future point at which
    ``entity_id`` "becomes" a real ``ForeignKey()`` once some target table
    lands — no single target table would ever make the constraint correct,
    because correctness here depends on a *runtime value*
    (``entity_type``), not on whether a *fixed* target table currently
    exists in the codebase. ``entity_id`` is therefore declared a plain
    ``Uuid`` column, permanently, with no ``ForeignKey()`` now or planned
    later. Referential integrity for this relationship — "does the row
    named by (entity_type, entity_id) actually exist in the table that
    entity_type names" — is a cross-table, value-dependent invariant no
    ``CHECK``/``ForeignKey`` on this row alone can express (the same
    "app/validation enforces what the schema cannot" split already
    established for ``customer_rep_assignment``'s no-overlap rule and
    ``costing_method_config``'s lock-eligibility rule), so it is enforced
    at the service layer instead.

``entity_type`` — plain token column, deliberately NO CHECK vocabulary:
    The task instructions ask for a ``CHECK`` bounding ``entity_type`` to a
    vocabulary of currently-existing or ERD-named candidate target tables,
    *if* the ERD gives a precise enough list — and explicitly call for this
    decision to be documented either way. Two independent facts, checked
    directly against the current state of this codebase and the ERD's own
    text, together rule that path out here:

    1. **None of the ERD's own illustrative examples exist as tables in
       this codebase yet.** The Purpose line's four named candidates —
       "adjustments" (a future ``stock_adjustment`` table), "returns" (a
       future ``customer_return`` table), "disputes", and "KYC docs" — have
       *no* corresponding model file anywhere under ``database/models/`` at
       the time this model is written (confirmed by directory listing: no
       ``stock_adjustment.py``, ``customer_return.py``, ``dispute*.py``, or
       ``kyc*.py`` exists). Writing a ``CHECK ... IN ('stock_adjustment',
       'customer_return', 'dispute', 'kyc_document')`` today would
       reference table names that do not exist anywhere in this schema —
       actively misleading (implying integration work that has not
       happened) rather than merely incomplete, and a guaranteed-stale
       constraint the moment any of those tables is actually built under a
       different literal name than guessed here.
    2. **The ERD explicitly declines to pin this down**, in its own words:
       "Left open by design since this is a logical ERD" (line 146,
       quoted in full above). This is not a case of the ERD simply omitting
       detail the way, say, ``price_list``'s missing ``Unique:`` line was
       silent by omission — line 146 is a direct, affirmative statement
       that physical-design decisions for exactly this polymorphic pattern
       are intentionally deferred past the logical-ERD stage. Fabricating a
       CHECK vocabulary here would be overriding that explicit instruction,
       not filling a silent gap.
    3. The Purpose line's own trailing "etc." after its four examples
       confirms the list was illustrative from the start, not meant as an
       exhaustive candidate-value enumeration even within the ERD's own
       prose.

    Given both signals point the same direction, ``entity_type`` is
    declared ``type_token_type()`` (``VARCHAR(40)``) — the same
    "polymorphic / enum-style token discriminator" factory
    ``inventory_transaction.reference_type`` already uses for an
    analogous polymorphic-discriminator column, per that factory's own
    docstring — ``NOT NULL``, but with **no** ``CheckConstraint`` bounding
    its values. This mirrors ``system_config.category``'s own "open,
    runtime-extensible label set, not a fixed enum" reasoning, but for a
    stronger, ERD-stated reason here (explicit "left open by design"
    language) rather than merely the absence of a ``PART A`` block.

Composite index on ``(entity_type, entity_id)`` — per PART M's direct
instruction, template borrowed from ``approval_request``'s own PART K entry:
    PART K does not spell out ``attachment`` in its own per-table
    enumerated list (only ``audit_log`` -> ``(entity_type, entity_id,
    occurred_at)`` and ``approval_request`` -> ``(entity_type, entity_id)``
    are given literal composite shapes there), but PART M's summary
    sentence (quoted in full above) names ``attachment`` explicitly among
    the five polymorphic-reference tables and states plainly that "each
    should carry its composite index (PART K)" — an affirmative instruction
    covering this table even though PART K's own line-item list only
    detailed two of the five by name. ``approval_request``'s own PART K
    entry, ``(entity_type, entity_id)`` with no third column, is the
    closest concrete template PART K actually gives for a *bare* polymorphic
    pair (as opposed to ``audit_log``'s three-column, time-ordered variant),
    and is used here unmodified: a non-unique composite ``Index`` on
    ``(entity_type, entity_id)`` via ``idx_index_name`` +
    ``composite_descriptor``, supporting the same "find every attachment
    evidencing this specific record" query ``approval_request``'s own index
    supports for approval history.

``uploaded_by`` — real FK, ``app_user`` already exists:
    Ordinary ``ForeignKey("app_user.id")``, the same treatment every other
    FK column pointing at an existing table receives in this codebase
    (contrast the *polymorphic* ``entity_id`` above, which can never be a
    real FK for the structural reasons given there — ``uploaded_by`` has
    exactly one, single, already-existing target table, so it is a
    completely ordinary FK, not a polymorphic or deferred case at all).
    The ERD's ``FK:`` line gives no explicit nullable annotation for
    ``uploaded_by`` (contrast M16's own ``generated_by -> app_user
    (nullable — system-generated)``, which *is* explicitly annotated
    nullable two lines below M14 in the same ERD section) — so, absent
    that annotation, ``uploaded_by`` is declared ``NOT NULL``: unlike
    M16's system-generated documents, M14's attachments are, per the
    Purpose line, user-supplied "supporting evidence", uploaded by a real
    person every time.

``file_name`` — human-facing original filename:
    ``name_type()`` -> ``VARCHAR(160)``, this codebase's factory for
    "human display name" columns (``product.name``/``warehouse.name``/
    full names, per its own docstring) — the closest existing semantic fit
    for an uploaded file's original display name, comfortably wider than
    any realistic filename while still bounded. Declared ``NOT NULL`` — an
    attachment row with no filename is not a meaningful uploaded file.

``mime_type`` — ``mime_type_type()`` -> ``VARCHAR(120)``:
    This factory's own docstring names ``attachment.mime_type`` by exact
    column name as its motivating use case
    ("Maps to ``StringLength.MIME_TYPE`` (``attachment.mime_type`` and
    adjacent format tokens)") — a direct, spec-literal match, not an
    inference. Declared ``NOT NULL``: content-type is a fact known at
    upload time (the client/browser always supplies one, defaulting to
    ``application/octet-stream`` if genuinely unknown), so there is no
    legitimate "unknown mime type" state worth modeling as ``NULL`` here.

``size_bytes`` — ``sqlalchemy.BigInteger()``, per direct instruction:
    File sizes can exceed the 32-bit signed-``INTEGER`` range (>2 GiB) for
    legitimate large attachments, so ``BigInteger`` is used directly —
    the same plain-``BigInteger`` treatment already given to
    ``inventory_balance_snapshot.last_transaction_seq`` and
    ``inventory_transaction.sequence_no`` for other unbounded/large-range
    counters in this codebase. Declared ``NOT NULL`` — an attachment row is
    only ever created once the file itself has finished uploading and its
    size is known.

``storage_key`` — ``storage_key_type()`` -> ``VARCHAR(512)``, column-level
``unique=True``:
    This factory's own docstring names ``attachment.storage_key`` by exact
    column name as its motivating use case ("S3-compatible object keys for
    ``attachment.storage_key`` / ``generated_document.storage_key``") — a
    direct, spec-literal match. Per the ERD's bare ``Unique: storage_key``
    line (a single column, not a tuple — the same shape already established
    for ``system_config.key`` / ``product_serial.serial_number``),
    ``storage_key`` is given column-level ``unique=True``, rendering
    ``uq_attachment_storage_key`` via the shared naming convention — not a
    composite constraint. Declared ``NOT NULL``: every attachment row
    corresponds to an actual stored object, so a row can never exist
    without the key identifying where that object lives.

``checksum`` — ``sqlalchemy.CHAR(HASH_HEX_LENGTH)``, imported directly from
``database.constants``, NOT a ``database.types`` factory:
    ``HASH_HEX_LENGTH``'s own module comment in ``database/constants.py``
    names "attachment checksum" explicitly, alongside
    ``inventory_transaction``/``customer_ledger_entry``/``commission``
    ``row_hash``/``prev_hash``, as one of the columns this exact constant
    is meant for — a direct, spec-literal match by name, mirroring
    ``inventory_transaction.py``'s own already-established precedent and
    reasoning for consuming ``HASH_HEX_LENGTH`` directly rather than via a
    ``database/types.py`` factory: ``types.py``'s own docstring flags
    ``HASH_HEX_LENGTH`` as a *deliberate omission* from that module (a
    module-level constant, not a ``NumericPrecision``/``StringLength``
    member), explicitly leaving the model layer to consume it directly —
    which is what this model does, exactly as ``inventory_transaction.py``
    already does for ``prev_hash``/``row_hash``. Declared ``NOT NULL``: a
    checksum is computed at upload time from the file's actual bytes, so
    every stored attachment has one by the time its row exists.

Soft delete — ``deleted_at``, per direct ERD instruction:
    The ERD's own ``Business constraints:`` line states this plainly:
    "soft-deletable (deleted_at) — unlike ledger/history tables, a
    wrongly-uploaded attachment may legitimately be removed without
    breaking financial integrity." ``deleted_at`` is declared directly, the
    same nullable-timezone-aware-timestamp pattern ``product.py`` (M1) /
    ``warehouse.py`` (M4) already establish for this codebase's
    soft-delete columns: ``NULL`` denotes an attachment that has not been
    (soft-)deleted.

Audit-column family — ``UniversalAuditColumns`` (UAC), per instruction:
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Attachment`` uses UAC and opts its ``version`` column
    into SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), matching every other UAC-using model in this codebase.

Naming convention:
    ``uploaded_by`` uses ``fk_index_name`` normally ->
    ``fk_attachment_uploaded_by_app_user_id``. ``storage_key`` uses
    column-level ``unique=True`` -> ``uq_attachment_storage_key`` (see
    dedicated section above — NOT composite). The polymorphic composite
    index uses ``idx_index_name("attachment", composite_descriptor(
    ["entity_type", "entity_id"]))`` -> ``idx_attachment_entity_type_entity_id``.
    ``entity_type``/``entity_id`` carry no ``CheckConstraint``/
    ``ForeignKey`` of their own (see dedicated sections above).

Column-type choices:

* ``entity_type`` -- ``type_token_type()`` -> ``VARCHAR(40)``, no CHECK
  (see dedicated section above).
* ``entity_id`` -- plain ``sqlalchemy.Uuid``, no ``ForeignKey()`` (see
  dedicated section above).
* ``uploaded_by`` -- FK to ``app_user.id``, ``NOT NULL``.
* ``file_name`` -- ``name_type()`` -> ``VARCHAR(160)``.
* ``mime_type`` -- ``mime_type_type()`` -> ``VARCHAR(120)``.
* ``size_bytes`` -- plain ``sqlalchemy.BigInteger()``.
* ``storage_key`` -- ``storage_key_type()`` -> ``VARCHAR(512)``,
  column-level ``unique=True``.
* ``checksum`` -- plain ``sqlalchemy.CHAR(HASH_HEX_LENGTH)``, imported
  directly from ``database.constants`` (see dedicated section above).
* ``deleted_at`` -- ``DateTime(timezone=True)``, nullable.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, Index
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.constants import HASH_HEX_LENGTH
from database.mixins import UniversalAuditColumns
from database.naming import composite_descriptor, fk_index_name, idx_index_name
from database.types import (
    mime_type_type,
    name_type,
    storage_key_type,
    type_token_type,
)


class Attachment(Base, UniversalAuditColumns):
    """``M14 — attachment`` — generic polymorphic file attachment (Classification: M + soft-deletable)."""

    __tablename__ = "attachment"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ entity_type
    # Polymorphic discriminator -- deliberately no CHECK vocabulary. See
    # module docstring's dedicated "entity_type" section.
    entity_type: Mapped[str] = mapped_column(
        type_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- entity_id
    # Polymorphic reference -- permanently plain, no ForeignKey(). This is
    # NOT a deferred-FK case; see module docstring's dedicated
    # "entity_type / entity_id" section for why no future table landing
    # will ever turn this into a real FK.
    entity_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        nullable=False,
    )

    # ------------------------------------------------------------ uploaded_by
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("attachment", "uploaded_by", "app_user"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- file_name
    file_name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- mime_type
    mime_type: Mapped[str] = mapped_column(
        mime_type_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- size_bytes
    size_bytes: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )

    # ------------------------------------------------------------ storage_key
    # Column-level unique=True -- independently unique, NOT composite. See
    # module docstring's dedicated "storage_key" section.
    storage_key: Mapped[str] = mapped_column(
        storage_key_type(),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------------- checksum
    # CHAR(HASH_HEX_LENGTH) imported directly from database.constants --
    # deliberate omission from database/types.py factories (see module
    # docstring, mirroring inventory_transaction.prev_hash/row_hash).
    checksum: Mapped[str] = mapped_column(
        CHAR(HASH_HEX_LENGTH),
        nullable=False,
    )

    # ------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker; NULL means not soft-deleted. Per
    # the ERD's explicit "Business constraints: soft-deletable" line.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # PART M's direct instruction: every polymorphic (entity_type,
        # entity_id) table "should carry its composite index (PART K)".
        # Template borrowed from approval_request's own PART K entry --
        # see module docstring.
        Index(
            idx_index_name(
                "attachment",
                composite_descriptor(["entity_type", "entity_id"]),
            ),
            "entity_type",
            "entity_id",
        ),
    )


__all__ = ["Attachment"]
