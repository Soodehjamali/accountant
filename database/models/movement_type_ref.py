"""``R4 — movement_type_ref`` ORM model (runtime-editable movement-type catalog).

Authority: docs/06_ERD.md, PART B → ``R4 — movement_type_ref``::

    R4 — movement_type_ref
    Purpose: Runtime-editable catalog of inventory movement types with sign
             convention + description.
    PK: id | code unique
    Important fields: code, sign (+1/−1), label
    Classification: R

…backed by the PART A ``MovementType`` value set (enforced here only by the
chosen column *width*, not a CHECK — the catalog is runtime-editable per the
ERD, so its values are NOT bounded by a CHECK the way a closed enum would be)::

    MovementType = RECEIPT_FROM_PRODUCTION, TRANSFER_IN, TRANSFER_OUT,
        SALE_OUT, SALE_RETURN_IN, ADJUSTMENT_POSITIVE, ADJUSTMENT_NEGATIVE,
        DAMAGED_OUT, FACTORY_DIRECT_SHIPMENT, INITIAL_OPENING_BALANCE, REVERSAL,
        CONSIGNMENT_SELLTHROUGH_OWNERSHIP

This entity has **no entry in docs/07_DATABASE_SPEC.md** — PART B reference
tables were not ported into the physical spec; the ERD is the source of truth
for ``movement_type_ref``'s own columns (``movement_type_id`` appears as an FK
target on ``inventory_transaction`` in the spec, and the spec pins the rule
"``signed_quantity``'s sign must match ``movement_type.sign``").

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` / ``version``.
    The ERD's §0.2 Governing Design Decisions states audit fields are stored by
    *every* table, so R-class editable reference tables adopt UAC. A movement-type
    catalog is explicitly "runtime-editable" per the ERD, so it gets UAC. Like
    ``currency`` / ``reason_code_ref``, it deliberately carries **no** ``deleted_at``
    / soft-delete: the ERD does not list a soft-delete column for R4, and a
    reference catalog is retired by discontinuing use, not soft-deleted
    (``UniversalAuditColumns`` as defined in ``database.mixins`` already carries
    no ``deleted_at``, so the mixin is used as-is).

Optimistic locking:
    ``__mapper_args__ = {"version_id_col": "version"}`` opts the model into the
    UAC ``version`` column as the SQLAlchemy row-version concurrency token (per
    ``database.mixins``'s documented opt-in mechanism — the mixin supplies the
    column, the model wires the mapper). Same pattern as ``currency`` /
    ``reason_code_ref``.

Naming convention:
    The column-level ``unique=True`` on ``code`` auto-named via the shared
    ``MetaData`` naming convention to ``uq_movement_type_ref_code``
    (``uq_%(table_name)s_%(column_0_name)s``). The ``sign`` range CHECK is named
    via ``database.naming.ck_index_name("movement_type_ref", "sign_values")``:
    that helper returns the bare descriptor ``sign_values`` (see its docstring —
    ``NAMING_CONVENTION["ck"]`` supplies the ``ck_<table>_`` prefix at compile
    time), rendering ``ck_movement_type_ref_sign_values`` verbatim.

Column-type choices (prefer existing ``database.types`` helpers over raw
``String(N)`` literals — no length invented):

* ``code`` — ``type_token_type()`` → ``VARCHAR(40)``. Width chosen by
  *measuring the PART A MovementType enum*: the longest token is
  ``CONSIGNMENT_SELLTHROUGH_OWNERSHIP`` (33 chars), which exceeds
  ``STATE_TOKEN`` (16) and ``STATE_TOKEN_LONG`` (24). The smallest existing
  member wide enough is ``StringLength.TYPE_TOKEN`` (40) — and its documented
  purpose is "polymorphic / enum-style token discriminator and growing enum
  tokens", exactly a movement-type code. ``code_short_type`` is also 40 but
  semantically "SKU / warehouse short code" (wrong fit for a discriminating
  enum token). So ``type_token_type`` is the precise, non-invented fit.
* ``sign`` — ``SmallInteger``, CHECK-bounded to ``{-1, +1}`` per the ERD's
  "(+1/−1)" sign convention. ``SmallInteger`` is the right width for a sign
  (range ample for ±1); the CHECK enforces the convention referenced by the
  spec ("``signed_quantity``'s sign must match ``movement_type.sign``").
* ``label`` — ``name_type()`` → ``VARCHAR(160)``. Human-readable movement
  description; ``StringLength.NAME`` is the existing helper for "human display
  names". Same choice as ``reason_code_ref.label``.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, SmallInteger
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name
from database.types import name_type, type_token_type


class MovementTypeRef(Base, UniversalAuditColumns):
    """``R4 — movement_type_ref`` — inventory movement-type catalog (Classification: R)."""

    __tablename__ = "movement_type_ref"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token (mixins.py opt-in mechanism).
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- code
    # Movement-type enum token (PART A). Measuring the longest MovementType
    # value — CONSIGNMENT_SELLTHROUGH_OWNERSHIP (33 chars) — exceeds
    # STATE_TOKEN (16) and STATE_TOKEN_LONG (24), so ``type_token_type``
    # (VARCHAR(40)) is the smallest existing, non-invented helper that fits.
    code: Mapped[str] = mapped_column(
        type_token_type(),
        nullable=False,
        unique=True,
    )

    # ---------------------------------------------------------------- sign
    # +1 / -1 sign convention per the ERD; bounded to {-1, +1} by the CHECK in
    # __table_args__. The spec pins "signed_quantity's sign must match
    # movement_type.sign" — so this column is the authority for sign.
    sign: Mapped[int] = mapped_column(
        SmallInteger(),
        nullable=False,
    )

    # ---------------------------------------------------------------- label
    # Human-readable movement description; ``name_type`` (VARCHAR(160)) is the
    # existing helper for human display names.
    label: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # ----------------------------------------------------------------- constraints
    __table_args__ = (
        # CHECK: ``sign`` restricted to the ERD's (+1/−1) convention. Named via
        # the convention helper: ``ck_index_name`` returns the bare descriptor
        # ``sign_values`` and ``NAMING_CONVENTION["ck"]`` supplies the
        # ``ck_movement_type_ref_`` prefix at compile time, rendering
        # ``ck_movement_type_ref_sign_values`` verbatim.
        CheckConstraint(
            "sign IN (-1, 1)",
            name=ck_index_name("movement_type_ref", "sign_values"),
        ),
    )


__all__ = ["MovementTypeRef"]
