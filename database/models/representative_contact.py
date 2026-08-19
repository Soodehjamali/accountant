"""``M7 — representative_contact`` ORM model (rep contact relationships: phone, email).

Authority: ``06_ERD.md``, line 29 → ``M7 — representative_contact``::

    M7 — representative_contact
    Purpose: Rep contact relationships (phone, email).
    PK: id
    FK: representative_id → representative
    Important fields: kind (PHONE/EMAIL), value, is_primary
    Unique: (representative_id, kind, value)
    Classification: M + soft-deletable

Same gap as every other table with no dedicated spec section so far
(``warehouse_location.py`` (M5), ``discount.py`` (H3), ``price_list.py``
(C3), etc.): ``06_ERD.md`` is ``representative_contact``'s sole authority --
``representative_contact`` has no detailed section in
``07_DATABASE_SPEC.md`` (confirmed by search: no
``07_DATABASE_SPEC.md`` mentions of "representative_contact" at all).

Sibling table, not yet built:
    ``06_ERD.md`` line 31 defines an M9 — ``customer_contact`` with the
    *identical* shape (``kind``, ``value``, ``is_primary``, the same
    ``(fk_id, kind, value)`` unique pattern, the same ``M +
    soft-deletable`` classification) against ``customer`` instead of
    ``representative``. ``customer_contact`` does not exist in this
    codebase yet (no ``database/models/customer_contact.py``), so there is
    no already-built sibling to mirror column-for-column here; this
    model's own choices (below) are made directly from the ERD line, not
    copied from a precedent that doesn't exist. Should ``customer_contact``
    be built later, it should mirror this model's choices (the ERD gives
    both tables the same shape).

FK is real from the outset:
    ``representative`` already exists in this codebase, so
    ``representative_id`` is declared as a real ``ForeignKey()`` from the
    start -- no deferred-FK section to write for this table. Not marked
    nullable in the ERD's ``FK:`` line, so declared ``NOT NULL`` -- a
    contact row with no owning representative is meaningless.

``kind`` -- inline two-member vocabulary, not a PART A-registered enum:
    Unlike ``PriceType`` / ``DiscountType`` / ``OrderState`` etc. (each
    formally listed in ``06_ERD.md``'s own ``PART A -- ENUMS`` section),
    ``kind``'s ``PHONE`` / ``EMAIL`` vocabulary appears only inline on this
    table's own ERD line (and identically on ``customer_contact``'s) -- it
    is not a named, separately-registered PART A enum. This changes
    nothing about how it's modeled: ``state_token_type()``
    (``VARCHAR(16)``) with a CHECK, the exact same treatment every
    PART A-registered vocabulary column in this codebase already receives
    (e.g. ``discount.discount_type``, ``price_list.price_type``) -- just
    sourced directly from this table's own ERD line instead of a separate
    PART A lookup, since the ERD gives the vocabulary either way.

``value`` -- type-width choice, wide enough for either contact kind:
    Neither ``code_short_type()`` (``VARCHAR(40)``, this codebase's
    "short business-facing identifier" factory -- used for ``warehouse
    .code`` / ``warehouse_location.code``) nor
    ``name_type()`` (``VARCHAR(160)``, "human display names") is the right
    semantic fit for a raw contact value that must hold *either* a phone
    number (short) *or* an email address (RFC 5321 allows up to 254
    characters, though real-world addresses are usually far shorter).
    ``description_type()`` (``VARCHAR(255)``) is used instead -- not
    because this value is a "description" in the same sense as
    ``price_list.owner_scope`` / ``price_history.reason``, but because it
    is this codebase's widest existing bounded-``VARCHAR`` factory, and
    255 comfortably covers the theoretical email-address ceiling with room
    to spare for a phone number in any format (with extension, country
    code, formatting characters, etc.) -- the same "closest existing
    factory, not a bespoke new type" treatment given to every other
    placeholder-width column in this codebase (e.g.
    ``order.fulfillment_mode``). ``NOT NULL`` -- a contact row with no
    actual contact value is meaningless, and the ERD gives no nullable
    annotation for it.

``is_primary`` -- boolean flag, default ``false``:
    A plain ``Boolean``, ``NOT NULL``, defaulting to ``false`` -- unlike
    ``price_list.is_active`` (which defaults ``true``, since a newly
    created price list is usable by default), "primary" is an explicit
    designation a representative or the application makes deliberately
    among several contacts of the same ``kind``, not the default state of
    every newly-added contact row; defaulting new rows to ``false`` avoids
    silently creating multiple simultaneous "primary" contacts of the same
    kind by default. (No ``UniqueConstraint``/partial-unique-index enforces
    "at most one primary per (representative_id, kind)" -- the ERD gives no
    such constraint for this table, so none is fabricated, the same
    "don't invent a schema rule the ERD doesn't specify" restraint already
    applied to ``discount``'s and ``price_list``'s absent/vague
    ``Unique:`` lines.)

Unique constraint -- literal ERD column list, ordinary composite case (NOT
a naming trap):
    ``UniqueConstraint("representative_id", "kind", "value")`` via
    ``uq_index_name`` + ``composite_descriptor`` -- the ERD gives this
    constraint's columns explicitly, so the standard helper output is used
    as-is with no override, the same ordinary treatment
    ``warehouse_location``'s own literal composite uniqueness already
    received.

No CHECK given in the ERD beyond the ``kind`` vocabulary:
    ``ck_representative_contact_kind_values`` (``kind IN ('PHONE',
    'EMAIL')``) is the only CHECK on this table -- ``value`` is free text
    with no format-validation CHECK (e.g. no email-shape regex constraint;
    format validation for a phone-vs-email value is a service-layer
    concern, not something a portable CHECK should encode), and
    ``is_primary`` is a plain boolean with no vocabulary to bound.

Soft delete -- direct ``deleted_at``, same pattern as
``warehouse_location.py``/``warehouse.py``/``product.py``:
    Per the ERD's own ``"M + soft-deletable"`` classification (the exact
    same classification tag ``warehouse_location.py`` carries and was
    explicitly instructed to mirror here), a nullable, timezone-aware
    ``TIMESTAMPTZ`` ``deleted_at`` column is declared directly -- ``NULL``
    meaning not deleted -- with no soft-delete-query helper/mixin to lean
    on (per this codebase's established convention, service-layer query
    filtering handles the rest).

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Plain ``M`` (master data) classification with the soft-delete
    qualifier -- an ordinary mutable master record, the same reasoning
    already established for ``warehouse_location.py`` / ``warehouse.py`` /
    ``product.py`` (all ``M + soft-deletable``, all UAC).
    ``RepresentativeContact`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), consistent with every other UAC-using model in this
    codebase.

Naming convention:
    ``representative_id`` uses ``fk_index_name`` normally ->
    ``fk_representative_contact_representative_id_representative_id``.
    The unique constraint uses ``uq_index_name`` + ``composite_descriptor``
    as an ordinary composite case ->
    ``uq_representative_contact_representative_id_kind_value``. The CHECK
    uses ``ck_index_name`` normally ->
    ``ck_representative_contact_kind_values``.

Column-type choices:

* ``kind`` -- ``state_token_type()`` -> ``VARCHAR(16)``.
* ``value`` -- ``description_type()`` -> ``VARCHAR(255)`` (see dedicated
  note above).
* ``is_primary`` -- ``sqlalchemy.Boolean``, ``NOT NULL DEFAULT false``.
* ``deleted_at`` -- ``DateTime(timezone=True)``, nullable, no default.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, uq_index_name
from database.types import description_type, state_token_type


class RepresentativeContact(Base, UniversalAuditColumns):
    """``M7 — representative_contact`` — rep contact relationships (phone, email) (Classification: M + soft-deletable)."""

    __tablename__ = "representative_contact"

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
            name=fk_index_name("representative_contact", "representative_id", "representative"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------------- kind
    # Inline PHONE/EMAIL vocabulary -- see module docstring's dedicated
    # section (not a separately PART A-registered enum, same treatment
    # regardless).
    kind: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------------------- value
    # description_type() -- widest existing bounded factory, wide enough
    # for either a phone number or an email address. See module
    # docstring's dedicated section.
    value: Mapped[str] = mapped_column(
        description_type(),
        nullable=False,
    )

    # ------------------------------------------------------------------ is_primary
    is_primary: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    # -------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker (same pattern as
    # warehouse_location.py / warehouse.py / product.py); NULL means not
    # soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- ordinary composite case, literal ERD column triple.
        UniqueConstraint(
            "representative_id",
            "kind",
            "value",
            name=uq_index_name(
                "representative_contact",
                composite_descriptor(("representative_id", "kind", "value")),
            ),
        ),
        # CHECK: kind vocabulary -- the only CHECK on this table, see
        # module docstring's dedicated section.
        CheckConstraint(
            "kind IN ('PHONE', 'EMAIL')",
            name=ck_index_name("representative_contact", "kind_values"),
        ),
    )


__all__ = ["RepresentativeContact"]
