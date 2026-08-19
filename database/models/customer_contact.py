"""``M9 — customer_contact`` ORM model (multiple contacts per customer).

Authority: ``06_ERD.md``, line 31 → ``M9 — customer_contact``::

    M9 — customer_contact
    Purpose: Multiple contacts per customer.
    PK: id
    FK: customer_id → customer
    Important fields: kind, value, is_primary
    Unique: (customer_id, kind, value)
    Classification: M + soft-deletable

Same gap as every other table with no dedicated spec section so far
(``representative_contact.py`` (M7), ``warehouse_location.py`` (M5),
``discount.py`` (H3), etc.): ``06_ERD.md`` is ``customer_contact``'s sole
authority -- ``customer_contact`` has no detailed section in
``07_DATABASE_SPEC.md`` (confirmed by search: no ``07_DATABASE_SPEC.md``
mentions of "customer_contact" at all).

Sibling table -- deliberate mirror of ``representative_contact.py`` (M7),
now that it exists:
    ``06_ERD.md`` line 29 defines ``M7 -- representative_contact`` with the
    *identical* shape (``kind``, ``value``, ``is_primary``, the same
    ``(fk_id, kind, value)`` unique pattern, the same ``M +
    soft-deletable`` classification) against ``representative`` instead of
    ``customer``. Unlike the situation when ``representative_contact.py``
    itself was built (where no sibling existed yet to mirror),
    ``representative_contact.py`` now exists in this codebase -- so this
    model deliberately mirrors its column-for-column choices rather than
    re-deriving them independently from the ERD line, per this task's own
    explicit instruction. Every reasoning note below is therefore the same
    reasoning already recorded on ``representative_contact.py``, restated
    here for ``customer_contact``'s own columns/names; the only structural
    difference between the two tables is the owning FK
    (``customer_id -> customer.id`` here, ``representative_id ->
    representative.id`` there) and, consequently, every name derived from
    it.

FK is real from the outset:
    ``customer`` already exists in this codebase, so ``customer_id`` is
    declared as a real ``ForeignKey()`` from the start -- no deferred-FK
    section to write for this table. Not marked nullable in the ERD's
    ``FK:`` line, so declared ``NOT NULL`` -- a contact row with no owning
    customer is meaningless. (Note: ``customer`` is documented elsewhere in
    this ERD, per ``06_ERD.md``'s own Aggregate Roots section, as its own
    independent aggregate root that does *not* own ``invoice``/``order`` --
    but it *does* own ``customer_contact`` per that same section's explicit
    listing, so this FK relationship is the intended ownership edge, not an
    exception to that independence.)

``kind`` -- inline two-member vocabulary, not a PART A-registered enum:
    Identical situation to ``representative_contact.kind``: ``PHONE`` /
    ``EMAIL`` appears only inline on this table's own ERD line (and
    identically on ``representative_contact``'s), not as a named PART A
    enum. Modeled the same way regardless: ``state_token_type()``
    (``VARCHAR(16)``) with a CHECK.

``value`` -- type-width choice, wide enough for either contact kind:
    ``description_type()`` (``VARCHAR(255)``) -- this codebase's widest
    existing bounded-``VARCHAR`` factory, comfortably covering both a phone
    number and the theoretical RFC 5321 email-address ceiling, the same
    choice ``representative_contact.value`` already made for the identical
    reason. ``NOT NULL`` -- a contact row with no actual contact value is
    meaningless.

``is_primary`` -- boolean flag, default ``false``:
    A plain ``Boolean``, ``NOT NULL``, defaulting to ``false`` -- the same
    reasoning as ``representative_contact.is_primary``: "primary" is an
    explicit designation among several contacts of the same ``kind``, not
    the default state of every newly-added contact row. No
    ``UniqueConstraint``/partial-unique-index enforces "at most one primary
    per (customer_id, kind)" here either -- the ERD gives no such
    constraint for this table (or its M7 sibling), so none is fabricated.

Unique constraint -- literal ERD column list, ordinary composite case (NOT
a naming trap):
    ``UniqueConstraint("customer_id", "kind", "value")`` via
    ``uq_index_name`` + ``composite_descriptor`` -- the ERD gives this
    constraint's columns explicitly, so the standard helper output is used
    as-is with no override, the same ordinary treatment
    ``representative_contact``'s own literal composite uniqueness already
    received.

No CHECK given in the ERD beyond the ``kind`` vocabulary:
    ``ck_customer_contact_kind_values`` (``kind IN ('PHONE', 'EMAIL')``) is
    the only CHECK on this table -- ``value`` is free text with no
    format-validation CHECK, and ``is_primary`` is a plain boolean with no
    vocabulary to bound. Same reasoning as
    ``representative_contact``'s own CHECK section.

Soft delete -- direct ``deleted_at``, same pattern as
``representative_contact.py``/``warehouse_location.py``/``warehouse.py``/
``product.py``:
    Per the ERD's own ``"M + soft-deletable"`` classification, a nullable,
    timezone-aware ``TIMESTAMPTZ`` ``deleted_at`` column is declared
    directly -- ``NULL`` meaning not deleted -- with no soft-delete-query
    helper/mixin to lean on, per this codebase's established convention.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    Plain ``M`` (master data) classification with the soft-delete
    qualifier -- an ordinary mutable master record, the same reasoning
    already established for ``representative_contact.py`` /
    ``warehouse_location.py`` / ``warehouse.py`` / ``product.py`` (all
    ``M + soft-deletable``, all UAC). ``CustomerContact`` uses UAC and
    opts its ``version`` column into SQLAlchemy optimistic locking
    (``__mapper_args__ = {"version_id_col": "version"}``), consistent with
    every other UAC-using model in this codebase.

Naming convention:
    ``customer_id`` uses ``fk_index_name`` normally ->
    ``fk_customer_contact_customer_id_customer_id``. The unique constraint
    uses ``uq_index_name`` + ``composite_descriptor`` as an ordinary
    composite case -> ``uq_customer_contact_customer_id_kind_value``. The
    CHECK uses ``ck_index_name`` normally ->
    ``ck_customer_contact_kind_values``.

Column-type choices:

* ``kind`` -- ``state_token_type()`` -> ``VARCHAR(16)``.
* ``value`` -- ``description_type()`` -> ``VARCHAR(255)``.
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


class CustomerContact(Base, UniversalAuditColumns):
    """``M9 — customer_contact`` — multiple contacts per customer (Classification: M + soft-deletable)."""

    __tablename__ = "customer_contact"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # -------------------------------------------------------------- customer_id
    customer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("customer_contact", "customer_id", "customer"),
        ),
        nullable=False,
    )

    # ---------------------------------------------------------------------- kind
    # Inline PHONE/EMAIL vocabulary -- see module docstring's dedicated
    # section (mirrors representative_contact.kind).
    kind: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------------------- value
    # description_type() -- widest existing bounded factory, wide enough
    # for either a phone number or an email address. See module
    # docstring's dedicated section (mirrors representative_contact.value).
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
    # representative_contact.py / warehouse_location.py / warehouse.py /
    # product.py); NULL means not soft-deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- ordinary composite case, literal ERD column triple.
        UniqueConstraint(
            "customer_id",
            "kind",
            "value",
            name=uq_index_name(
                "customer_contact",
                composite_descriptor(("customer_id", "kind", "value")),
            ),
        ),
        # CHECK: kind vocabulary -- the only CHECK on this table, see
        # module docstring's dedicated section.
        CheckConstraint(
            "kind IN ('PHONE', 'EMAIL')",
            name=ck_index_name("customer_contact", "kind_values"),
        ),
    )


__all__ = ["CustomerContact"]
