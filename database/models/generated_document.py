"""``M16 — generated_document`` ORM model (system-generated PDF/document storage).

Authority: ``06_ERD.md``, line 118 → ``M16 — generated_document``::

    M16 — generated_document
    Purpose: System-generated PDF/document storage (invoice PDFs, credit
             note PDFs, report exports) — distinct from user-uploaded
             attachment.
    PK: id
    FK: entity_type + entity_id (polymorphic: invoice | credit_note |
        report_run), generated_by → app_user (nullable — system-generated)
    Important fields: document_type (INVOICE_PDF/CREDIT_NOTE_PDF/
                      REPORT_EXPORT/...), format (PDF/CSV/XLSX),
                      storage_key, generated_at, version
    Unique: storage_key
    Business constraints: immutable once generated — a corrected invoice
                          regenerates a new versioned document rather than
                          overwriting; superseded versions are retained
                          for audit
    Classification: H (append-only per version)

``06_ERD.md`` is M16's sole authority: like every other table with no
dedicated spec section so far (``attachment.py`` (M14), ``product_image.py``
(M15), ``report_definition.py`` (M17)), M16 has no detailed section in
``07_DATABASE_SPEC.md``.

Also see line 146 (PART G/architecture note, quoted in full in
``attachment.py``'s own docstring), which names ``generated_document``
explicitly, alongside ``attachment``, among the polymorphic-reference tables
whose *physical FK-enforcement mechanism* is "Left open by design since this
is a logical ERD" — directly relevant to the ``entity_id`` decision below.

``entity_type`` / ``entity_id`` — polymorphic, permanently plain columns, NO
``ForeignKey()``, same structural reasoning as ``attachment.py`` (M14):
    Identical reasoning to ``attachment.entity_id``'s own dedicated
    docstring section, not repeated in full here: a single SQL
    ``ForeignKey`` binds a column to exactly one target table at
    DDL-definition time, but this column's real target varies *per row* by
    whatever ``entity_type`` holds on that row — no fixed target table
    would ever make a ``ForeignKey()`` here correct, so this is not a
    temporary/deferred-FK case (contrast ``app_user.representative_id``
    before ``representative`` (M6) landed) but a permanent one. Line 146
    names ``generated_document`` by name alongside ``attachment`` for
    exactly this "physical design must choose... left open by design"
    note, so ``entity_id`` is declared a plain ``Uuid`` column with no
    ``ForeignKey()``, now or ever, mirroring ``attachment.entity_id``
    exactly.

``entity_type`` — CHECK-bounded to ``('invoice', 'credit_note',
'report_run')``, a deliberate DIVERGENCE from ``attachment.entity_type``'s
own no-CHECK decision:
    This is the one place M16 does *not* simply mirror M14. The distinction
    is textual, not structural — the underlying "no fixed FK target"
    problem is identical on both tables (see section above), but a
    ``CHECK`` constraint bounding a string column to a literal value list
    does not require any of those literal strings to correspond to a table
    that physically exists in this schema — unlike a ``ForeignKey()``,
    which needs a real target table to reference, a ``CHECK ... IN (...)``
    is purely a string-equality test against constants baked into the
    constraint itself. So "none of ``invoice``/``credit_note``/
    ``report_run`` exist as tables in this codebase yet" — true here, the
    same as it was true for every one of ``attachment``'s own illustrative
    examples — does **not** carry the same disqualifying weight it carried
    for ``attachment.entity_type``, because a CHECK's validity never
    depended on those tables existing in the first place.

    What actually matters is whether the ERD's own vocabulary is *closed*
    (an exhaustive, finite value set) or *open* (illustrative, extensible).
    Compared directly against ``attachment``'s own ``Purpose`` line:

    * ``attachment``: "supporting evidence for adjustments, returns,
      disputes, KYC docs, **etc.**" — the trailing "etc." is an explicit
      textual marker that the list is illustrative, not exhaustive, and
      more members are expected over time.
    * ``generated_document``: "``entity_type + entity_id`` (polymorphic:
      **invoice | credit_note | report_run**)" — three literal values,
      pipe-separated, presented as *the* set this FK line names, with
      **no** trailing "etc." or other open-endedness marker anywhere in
      this table's own ERD line. This reads as a closed enumeration: three
      named source-document kinds a *system-generated* document can ever
      be produced for, consistent with the Purpose line's own closed
      framing — "invoice PDFs, credit note PDFs, report exports" is a
      complete list of the three concrete document-generation pathways this
      table's Purpose line describes, not an illustrative sample of a
      larger open set.

    Given the vocabulary itself is textually closed here (regardless of
    whether the three referenced tables physically exist yet — a fact that
    is irrelevant to a CHECK, unlike a FK), ``entity_type`` receives an
    ordinary vocabulary ``CheckConstraint`` bounding it to exactly
    ``'invoice'`` / ``'credit_note'`` / ``'report_run'``, via
    ``ck_index_name``, the same idiom already used for every other
    explicit, closed ERD enum in this codebase
    (``discount.discount_type`` / ``price_list.price_type`` /
    ``product_serial.status`` / this table's own ``format`` column below).
    This is a genuinely different textual signal from ``attachment``'s own
    line, not an inconsistency between the two models: both tables apply
    the identical underlying rule ("CHECK-bound only what the ERD states as
    closed") to two different ERD lines that happen to differ in exactly
    the one respect (open vs. closed vocabulary) that rule is sensitive to.
    ``type_token_type()`` (``VARCHAR(40)``) is used for the column's width
    — matching ``attachment.entity_type``'s own factory choice for
    consistency across the two polymorphic-discriminator columns in this
    codebase — even though the actual CHECK-bounded values here are much
    shorter; width consistency with the sibling polymorphic column takes
    precedence over minimizing to the current value set, in case the
    business-approved vocabulary grows later (the CHECK can be altered
    without a column-width migration).

Composite index on ``(entity_type, entity_id)`` — same PART M instruction
already applied to ``attachment``:
    Line 220 (PART M) names ``generated_document`` explicitly, alongside
    ``attachment``, among the polymorphic-reference tables that "should
    carry its composite index (PART K)". Same non-unique composite
    ``Index`` via ``idx_index_name`` + ``composite_descriptor``, identical
    treatment to ``attachment.py``.

``generated_by`` — ``ForeignKey("app_user.id")``, nullable per direct,
explicit ERD annotation:
    ``app_user`` already exists in this codebase — ordinary, single-target
    FK (contrast the permanently-polymorphic ``entity_id`` above).
    Nullable per the ERD's own explicit parenthetical,
    "``(nullable — system-generated)``" — unlike ``attachment.uploaded_by``
    (declared ``NOT NULL`` because attachments are always user-uploaded
    "supporting evidence"), this table's Purpose line is itself
    "*System*-generated ... storage": scheduled/automated document
    generation (e.g. a nightly invoice-PDF regeneration job, or a
    scheduler-triggered report export per ``report_run``'s own
    ``triggered_by`` — nullable, "scheduler if null") has no human actor to
    record, so ``NULL`` legitimately means "the system generated this
    document, not a person".

``document_type`` — free token, deliberately NO CHECK vocabulary, same
reasoning as ``report_definition.output_format``:
    The ERD spells out three worked examples — ``INVOICE_PDF`` /
    ``CREDIT_NOTE_PDF`` / ``REPORT_EXPORT`` — but closes the list with an
    explicit trailing ``"/..."``, the identical open-endedness marker
    ``attachment``'s own Purpose line used ("etc."), just rendered as an
    ellipsis inside the enum-style list instead of prose. This is the
    reverse of the ``entity_type`` situation above: here the ERD's own
    textual marker signals an *open*, growing vocabulary, so — following
    the same "CHECK-bound only what the ERD states as closed" rule applied
    consistently throughout this docstring — no ``CheckConstraint`` is
    declared for ``document_type``, mirroring
    ``report_definition.output_format``'s own no-CHECK decision (there,
    the ERD gave no enumerated set at all for that specific column; here,
    the ERD gives *some* named values but explicitly marks the set
    non-exhaustive — both cases resolve to "open", just via different
    textual signals). ``type_token_type()`` (``VARCHAR(40)``) is used
    rather than ``state_token_type()`` -- although every worked example
    (``CREDIT_NOTE_PDF``, the longest, is 15 characters) would fit
    comfortably in the 16-char ``state_token_type()`` width, the ERD's own
    explicit "..." marks this as a *growing* enum-style token set, which is
    precisely ``type_token_type()``'s own documented use case ("polymorphic
    / enum-style token discriminators and growing enum tokens") rather than
    ``state_token_type()``'s "small enums" framing -- the same distinction
    already drawn between the two factories for ``attachment.entity_type``.
    Declared ``NOT NULL``: the ERD names it among ``Important fields:`` with
    no nullable annotation, and every generated document has a concrete
    kind of document it is, even if the schema does not enumerate every
    legal value.

``format`` — CHECK-bounded to exactly ``('PDF', 'CSV', 'XLSX')``, a genuinely
closed vocabulary:
    Unlike ``document_type`` immediately above, the ERD's own
    ``format (PDF/CSV/XLSX)`` parenthetical carries **no** trailing "..." or
    other open-endedness marker — three literal values, presented as the
    complete set. ``state_token_type()`` (``VARCHAR(16)``) fits every
    member with room to spare (``XLSX``, the longest, is 4 characters). A
    ``CheckConstraint`` via ``ck_index_name`` bounds the column to these
    three literal values, the same CHECK-bounded-enum idiom used
    consistently throughout this codebase. Declared ``NOT NULL`` — every
    generated document is stored in some concrete file format.

``storage_key`` — ``storage_key_type()`` -> ``VARCHAR(512)``, column-level
``unique=True``, same idiom as ``attachment.storage_key``:
    Per direct instruction. This factory's own docstring names
    ``generated_document.storage_key`` by exact column name as one of its
    two motivating use cases ("S3-compatible object keys for
    ``attachment.storage_key`` / ``generated_document.storage_key``") — a
    direct, spec-literal match. Per the ERD's bare ``Unique: storage_key``
    line (a single column, not a tuple), ``storage_key`` is given
    column-level ``unique=True``, rendering
    ``uq_generated_document_storage_key`` via the shared naming
    convention — not composite. Declared ``NOT NULL``: every row
    corresponds to an actual stored object.

``generated_at`` — ``NOT NULL DEFAULT now()``, per direct instruction:
    ``DateTime(timezone=True)``, ``server_default=func.now()`` — the same
    "spec-mandated exact shape" treatment already given to
    ``inventory_transaction.occurred_at`` for an analogous
    NOT-NULL-timestamped-at-creation column, and consistent with AAC's own
    ``created_at`` shape (though this is a distinct, business-named column,
    not AAC's own ``created_at`` — see the dedicated "``version``" section
    below for the parallel distinction AAC's own columns receive on this
    table).

``version`` — an explicit ERD BUSINESS column (document-regeneration
version number), NOT UAC's optimistic-lock ``version`` — no collision,
because this table does not use UAC at all:
    This is the one column on this table most likely to be misread by
    analogy to every other model built so far in this codebase, so it is
    addressed directly and explicitly, per instruction. Every UAC-using
    model already built (``commission_config``, ``costing_method_config``,
    ``system_config``, ``product_serial``, ``attachment``,
    ``product_image``, ``report_definition``, ...) gets its ``version``
    column *from the UAC mixin itself* — an ``Integer``, opted into
    SQLAlchemy's ``version_id_col`` optimistic-concurrency mechanism via
    each model's own ``__mapper_args__ = {"version_id_col": "version"}``,
    with a semantics of "how many times has *this row* been updated,
    checked automatically by the ORM on every ``UPDATE`` to detect
    lost-update races".

    ``generated_document`` uses **``AppendOnlyAuditColumns`` (AAC)**, not
    UAC (see dedicated "Audit-column family" section below) — and AAC, per
    its own docstring, *deliberately omits* a ``version`` column entirely
    ("Deliberately omits ``updated_at`` / ``updated_by`` / ``version``:
    these rows are never ``UPDATE``-d ... so there is no ... optimistic
    lock to guard against"). So there is no mixin-provided ``version``
    column on this model at all, and therefore **no naming collision or
    semantic ambiguity is possible** between AAC (which provides none) and
    this table's own column: the ``version`` declared directly on
    ``GeneratedDocument`` below is entirely and unambiguously this table's
    own business-domain field.

    The ERD's own ``Important fields:`` line names ``version`` directly,
    read together with the table's Purpose/Business-constraints text: "a
    corrected invoice regenerates a new versioned document rather than
    overwriting; superseded versions are retained for audit" and
    "Classification: H (append-only **per version**)". This ``version`` is
    therefore a plain business ``Integer`` counting *which numbered
    regeneration* a given row represents for its (``entity_type``,
    ``entity_id``) pair — e.g. the first PDF generated for a given invoice
    is version 1; if that invoice is later corrected and its PDF
    regenerated, the correction produces an entirely new row with
    ``version = 2``, while the original ``version = 1`` row is retained
    unmodified (append-only) rather than updated in place — exactly the
    "immutable once generated ... superseded versions are retained"
    business constraint. Declared ``NOT NULL`` with **no invented
    default**: the ERD gives no default value to transcribe for this
    column (unlike UAC's own ``version``, whose starting value of ``1`` is
    a documented, spec-derived constant --
    ``OPTIMISTIC_LOCK_VERSION_START`` in ``database/constants.py``, cited
    directly by ``mixins.py``), and this is a different column serving a
    different purpose than that constant models — inventing a "starts at
    1" default here would be borrowing UAC's own convention for a
    superficially similar-looking but semantically distinct column, the
    same "don't invent a default with no textual basis for *this* column"
    discipline already applied to ``report_definition.output_format`` /
    ``customer_rep_assignment.priority``. The caller supplies the correct
    version number explicitly at insert time.

No CHECK beyond ``entity_type`` / ``format``:
    ``generated_by`` is a plain nullable FK with no additional business
    rule stated in the ERD line; ``storage_key`` is free text (bounded only
    by uniqueness); ``generated_at`` is a plain timestamp;
    ``document_type`` is deliberately open (see dedicated section above);
    ``version`` is a plain business integer with no stated bound.

Audit-column family — ``AppendOnlyAuditColumns`` (AAC), NOT UAC, per direct
instruction:
    ``generated_document`` is classified ``H (append-only per version)`` —
    the ERD's own Business-constraints line states this plainly:
    "immutable once generated ... superseded versions are retained for
    audit". This is the identical shape already established for
    ``price_history`` (H1) and ``order_status_history`` (T12/H5): a row, once
    written, is never ``UPDATE``-d — a correction produces a brand-new row
    (a new ``version``) rather than mutating the old one, so AAC's own
    "no ``updated_at`` / ``updated_by`` / ``version`` — these rows are
    never ``UPDATE``-d" rationale (``mixins.py``'s own docstring) applies
    here exactly as it does on those two tables. ``GeneratedDocument``
    therefore gets only ``created_at`` (``TIMESTAMPTZ NOT NULL DEFAULT
    now()``) and ``created_by`` (nullable ``UUID``, no FK per AAC's own
    documented "no FK until app_user lands" note — though ``app_user`` now
    exists in this codebase, AAC's own column is left as the mixin
    provides it, unmodified here, the same treatment already given to
    ``price_history``/``order_status_history``'s own inherited
    ``created_by``) from the mixin. No ``__mapper_args__`` /
    ``version_id_col`` is declared on this model — that mechanism belongs
    to UAC-using models only, and, as detailed in the dedicated
    ``version`` section above, would in any case be semantically wrong
    here even if AAC did provide a column of that name (it does not).

Naming convention:
    ``generated_by`` uses ``fk_index_name`` normally ->
    ``fk_generated_document_generated_by_app_user_id``. ``storage_key``
    uses column-level ``unique=True`` -> ``uq_generated_document_storage_key``
    (NOT composite). Both vocabulary CHECKs use ``ck_index_name`` -> bare
    descriptors ``entity_type_values`` / ``format_values``, rendering
    ``ck_generated_document_entity_type_values`` /
    ``ck_generated_document_format_values`` at compile time. The
    polymorphic composite index uses ``idx_index_name("generated_document",
    composite_descriptor(["entity_type", "entity_id"]))`` ->
    ``idx_generated_document_entity_type_entity_id``.

Column-type choices:

* ``entity_type`` -- ``type_token_type()`` -> ``VARCHAR(40)``,
  CHECK-bounded to ``invoice`` / ``credit_note`` / ``report_run`` (see
  dedicated section above -- DIVERGES from ``attachment.entity_type``).
* ``entity_id`` -- plain ``sqlalchemy.Uuid``, no ``ForeignKey()``.
* ``generated_by`` -- FK to ``app_user.id``, nullable.
* ``document_type`` -- ``type_token_type()`` -> ``VARCHAR(40)``, no CHECK
  (open vocabulary, per ERD's own "...").
* ``format`` -- ``state_token_type()`` -> ``VARCHAR(16)``, CHECK-bounded to
  ``PDF`` / ``CSV`` / ``XLSX``.
* ``storage_key`` -- ``storage_key_type()`` -> ``VARCHAR(512)``,
  column-level ``unique=True``.
* ``generated_at`` -- ``DateTime(timezone=True)``, ``NOT NULL``,
  ``server_default=func.now()``.
* ``version`` -- plain ``sqlalchemy.Integer``, ``NOT NULL``, no default
  (business column -- see dedicated section above, NOT UAC's
  ``version_id_col``).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name
from database.types import state_token_type, storage_key_type, type_token_type


class GeneratedDocument(Base, AppendOnlyAuditColumns):
    """``M16 — generated_document`` — system-generated PDF/document storage (Classification: H, append-only per version)."""

    __tablename__ = "generated_document"

    # NOTE: no __mapper_args__ / version_id_col here -- this table uses AAC,
    # not UAC, and has no ORM-managed optimistic-lock column at all. Its own
    # `version` column below is an unrelated, ERD-named business field. See
    # module docstring's dedicated "version" section.

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ entity_type
    # CHECK-bounded -- DIVERGES from attachment.entity_type's own no-CHECK
    # decision. See module docstring's dedicated section for why: the ERD's
    # vocabulary here is textually closed (no "etc."), unlike attachment's.
    entity_type: Mapped[str] = mapped_column(
        type_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- entity_id
    # Polymorphic reference -- permanently plain, no ForeignKey(). Same
    # structural reasoning as attachment.entity_id; see module docstring.
    entity_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        nullable=False,
    )

    # ----------------------------------------------------------- generated_by
    # Nullable per direct, explicit ERD annotation: "(nullable --
    # system-generated)".
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "app_user.id",
            name=fk_index_name("generated_document", "generated_by", "app_user"),
        ),
        nullable=True,
    )

    # ----------------------------------------------------------- document_type
    # Free token, deliberately no CHECK vocabulary -- ERD's own trailing
    # "..." marks this an open, growing enum. See module docstring.
    document_type: Mapped[str] = mapped_column(
        type_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- format
    # CHECK-bounded -- ERD's own (PDF/CSV/XLSX) carries no "..." marker;
    # genuinely closed vocabulary. See module docstring.
    format: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ------------------------------------------------------------ storage_key
    # Column-level unique=True -- independently unique, NOT composite. Same
    # idiom as attachment.storage_key. See module docstring.
    storage_key: Mapped[str] = mapped_column(
        storage_key_type(),
        nullable=False,
        unique=True,
    )

    # ----------------------------------------------------------- generated_at
    generated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # --------------------------------------------------------------- version
    # ERD-named BUSINESS column (document-regeneration version number) --
    # NOT UAC's optimistic-lock version_id_col. AAC provides no `version`
    # column at all, so there is no collision. See module docstring's
    # dedicated "version" section for the full distinction.
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('invoice', 'credit_note', 'report_run')",
            name=ck_index_name("generated_document", "entity_type_values"),
        ),
        CheckConstraint(
            "format IN ('PDF', 'CSV', 'XLSX')",
            name=ck_index_name("generated_document", "format_values"),
        ),
        # PART M's direct instruction, same as attachment: every
        # polymorphic (entity_type, entity_id) table "should carry its
        # composite index (PART K)".
        Index(
            idx_index_name(
                "generated_document",
                composite_descriptor(["entity_type", "entity_id"]),
            ),
            "entity_type",
            "entity_id",
        ),
    )


__all__ = ["GeneratedDocument"]
