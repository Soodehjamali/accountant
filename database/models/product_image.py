"""``M15 — product_image`` ORM model (product-specific images; specializes attachment).

Authority: ``06_ERD.md``, line 116 → ``M15 — product_image``::

    M15 — product_image
    Purpose: Product-specific images (catalog, variant swatches);
             specializes attachment with display concerns (sort order,
             primary flag).
    PK: id
    FK: product_id → product, attachment_id → attachment
    Important fields: sort_order, is_primary (bool), alt_text
    Unique: (product_id, sort_order); conditional uniqueness on
            (product_id, is_primary = true)
    Classification: M + soft-deletable

``06_ERD.md`` is M15's sole authority: like every other M-table so far
(``product.py`` (M1), ``product_lot.py`` (M2), ``product_serial.py`` (M3),
``attachment.py`` (M14)), M15 has no detailed section in
``07_DATABASE_SPEC.md``.

Relationship to ``attachment`` (M14) — specialization, not duplication:
    The Purpose line is explicit: this table "specializes attachment with
    display concerns (sort order, primary flag)". Per direct instruction,
    the physical file and its generic storage metadata — ``file_name``,
    ``mime_type``, ``size_bytes``, ``storage_key``, ``checksum`` — live
    entirely on ``attachment`` (M14) via the ``attachment_id`` FK; none of
    those columns are duplicated here. ``product_image`` adds only the
    columns the ERD's own ``Important fields:`` line names, all of which
    are display/ordering concerns specific to *how a product catalog
    presents* an already-uploaded file, not facts about the file itself:
    ``sort_order`` (catalog ordering), ``is_primary`` (which image is the
    product's hero/default image), and ``alt_text`` (accessibility text for
    *this particular product-image association* — the same underlying
    attachment could in principle be alt-texted differently per
    product/context, which is exactly why this belongs on the join/
    specialization row rather than on ``attachment`` itself).

``product_id`` — ``ForeignKey("product.id")``, ``NOT NULL``:
    ``product`` already exists in this codebase. The ERD's ``FK:`` line
    carries no nullable annotation for either FK on this table, and a
    product-image row with no product is meaningless, so both FKs are
    declared ``NOT NULL``.

``attachment_id`` — ``ForeignKey("attachment.id")``, ``NOT NULL``:
    ``attachment`` (M14) already exists in this codebase (built directly
    before this model). This is a completely ordinary, single-target FK —
    unlike ``attachment.entity_id``'s own permanently-polymorphic,
    FK-less column, ``product_image.attachment_id`` always points at
    exactly one target table (``attachment``), so it receives a normal
    ``ForeignKey()`` with no special treatment.

``sort_order`` — plain ``Integer``, per direct instruction:
    The ERD names ``sort_order`` with no vocabulary or bound beyond its
    role in the ``(product_id, sort_order)`` unique pair (see dedicated
    "Uniqueness" section below). Same plain, unbounded ``sqlalchemy.Integer``
    treatment already given to ``customer_rep_assignment.priority`` for an
    analogous "ERD names the field but gives it no default/vocabulary"
    ranking column. ``NOT NULL`` — a product-image row must have a
    determinate position in its product's image sequence to participate in
    the ``(product_id, sort_order)`` uniqueness rule at all — with no
    invented default (the same "no textual basis, don't invent one"
    discipline already applied to ``priority``): the caller must supply an
    explicit position when creating an image row.

``is_primary`` — boolean flag, default ``false``, exact same idiom as
``warehouse_assignment.is_primary`` / ``representative_contact.is_primary``
/ ``customer_contact.is_primary``:
    "Primary" is an explicit business flag naming which one image (if any)
    is the product's default/hero image — the identical semantic shape
    already established for "primary" flags elsewhere in this codebase.
    ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT false`` — so a bare
    ``INSERT`` with no explicit value still yields a valid, well-defined
    "not primary" row rather than requiring every call site to know this
    column exists, the same reasoning ``warehouse_assignment.is_primary``'s
    own docstring already gives.

``alt_text`` — accessibility text, ``description_type()``:
    Per direct instruction, uses ``description_type()`` -> ``VARCHAR(255)``,
    this codebase's factory for "free-text line descriptions"
    (``invoice_line.description`` / ``credit_note_line.description``, per
    its own docstring) — the closest existing semantic fit for a free-text,
    human-authored accessibility caption, comfortably sized for a real
    ``alt`` attribute without inviting paragraph-length content. Declared
    nullable: the ERD gives no explicit nullable annotation, but
    accessibility text is conventionally optional at the point an image is
    first attached (many real catalog workflows populate ``alt_text`` in a
    follow-up editorial pass, not atomically with the upload/attach step),
    and — unlike ``sort_order``/``is_primary`` — ``alt_text`` participates
    in no uniqueness or business rule that would require a value to be
    present for the row to be meaningful.

Uniqueness — two independent rules, per direct instruction, second rule
mirrors ``warehouse_assignment.py`` (C5) exactly:
    1. ``UniqueConstraint(product_id, sort_order)`` — an ordinary composite
       case, the literal ERD-named column pair, via ``uq_index_name`` +
       ``composite_descriptor``. No image position may repeat within the
       same product's sequence.
    2. Conditional "at most one primary image per product" — the ERD's own
       ``"conditional uniqueness on (product_id, is_primary = true)"``
       phrasing is structurally identical to
       ``warehouse_assignment``'s own ERD line,
       ``"conditional uniqueness on (representative_id, is_primary=true)"``,
       already implemented there as a ``postgresql_where``-filtered unique
       partial index: ``Index(idx_index_name("product_image",
       "one_primary_image"), "product_id", unique=True,
       postgresql_where=sa_text("is_primary = true"))``. The mechanism is
       reused verbatim (same shape ``warehouse_assignment`` /
       ``warehouse.idx_warehouse_one_active_factory`` already establish for
       this codebase's "at most one flagged row per group" idiom): rows
       where ``is_primary = false`` fall outside the partial index's
       ``WHERE`` predicate entirely and are therefore unconstrained by it
       (any number of non-primary images per product is fine), while rows
       where ``is_primary = true`` are uniquely indexed on ``product_id``
       alone, so at most one row per product may ever have
       ``is_primary = true``. Unlike ``costing_method_config.singleton_guard``
       (a schema-mechanism column with no independent business meaning,
       requiring an additional CHECK to close a bypass loophole),
       ``is_primary`` here is a real, independently-meaningful business
       flag exactly like ``warehouse_assignment.is_primary`` — both
       ``true`` and ``false`` are legitimate states for different rows
       simultaneously — so no extra CHECK is needed to make this partial
       index sound; the partial index alone is already the complete,
       correct implementation of the ERD's conditional rule.

No CHECK constraints:
    Neither FK carries an additional business rule beyond referential
    integrity, ``sort_order`` is an unbounded ranking integer, ``is_primary``
    is a plain boolean, and ``alt_text`` is free text — no vocabulary column
    exists on this table to bound.

Soft delete — ``deleted_at``, per direct instruction:
    Per the ERD's own ``Classification: M + soft-deletable``. ``deleted_at``
    is declared directly, the same nullable-timezone-aware-timestamp pattern
    ``product.py`` (M1) / ``warehouse.py`` (M4) / ``attachment.py`` (M14)
    already establish for this codebase's soft-delete columns: ``NULL``
    denotes a product-image association that has not been (soft-)deleted.
    Note this soft-delete flag belongs to the *association* row
    (``product_image``), independent of whether the underlying
    ``attachment`` row is itself soft-deleted — removing an image from a
    product's catalog display need not delete the underlying uploaded file,
    consistent with this table's own "specializes attachment with display
    concerns" framing.

Audit-column family — ``UniversalAuditColumns`` (UAC), per instruction:
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``ProductImage`` uses UAC and opts its ``version`` column
    into SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), matching every other UAC-using model in this codebase.

Naming convention:
    Both FKs use ``fk_index_name`` normally —
    ``fk_product_image_product_id_product_id`` /
    ``fk_product_image_attachment_id_attachment_id``. The composite
    ``UniqueConstraint`` uses ``uq_index_name`` + ``composite_descriptor`` ->
    ``uq_product_image_product_id_sort_order``. The conditional partial
    unique index uses ``idx_index_name("product_image", "one_primary_image")``
    -> ``idx_product_image_one_primary_image``.

Column-type choices:

* ``sort_order`` — plain ``sqlalchemy.Integer``, ``NOT NULL``, no default.
* ``is_primary`` — ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT false``.
* ``alt_text`` — ``description_type()`` -> ``VARCHAR(255)``, nullable.
* ``deleted_at`` — ``DateTime(timezone=True)``, nullable.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import composite_descriptor, fk_index_name, idx_index_name, uq_index_name
from database.types import description_type


class ProductImage(Base, UniversalAuditColumns):
    """``M15 — product_image`` — product-specific images; specializes attachment with display concerns (Classification: M + soft-deletable)."""

    __tablename__ = "product_image"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ product_id
    product_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product.id",
            name=fk_index_name("product_image", "product_id", "product"),
        ),
        nullable=False,
    )

    # --------------------------------------------------------- attachment_id
    # Ordinary, single-target FK -- unlike attachment.entity_id's own
    # permanently-polymorphic column, this always points at exactly one
    # table (attachment). See module docstring.
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "attachment.id",
            name=fk_index_name("product_image", "attachment_id", "attachment"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- sort_order
    sort_order: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )

    # ------------------------------------------------------------------ is_primary
    is_primary: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    # -------------------------------------------------------------- alt_text
    # Nullable -- optional accessibility text, no uniqueness/business rule
    # requires a value. See module docstring.
    alt_text: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )

    # ------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker; NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE #1 -- ordinary composite case, literal ERD column pair.
        UniqueConstraint(
            "product_id",
            "sort_order",
            name=uq_index_name(
                "product_image",
                composite_descriptor(("product_id", "sort_order")),
            ),
        ),
        # UNIQUE #2 -- conditional partial unique index, "at most one
        # primary image per product". Mirrors warehouse_assignment's own
        # idx_warehouse_assignment_one_primary_warehouse exactly -- see
        # module docstring's dedicated "Uniqueness" section.
        Index(
            idx_index_name("product_image", "one_primary_image"),
            "product_id",
            unique=True,
            postgresql_where=sa_text("is_primary = true"),
        ),
    )


__all__ = ["ProductImage"]
