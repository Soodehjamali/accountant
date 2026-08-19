"""``R11 — reason_code_ref`` ORM model (standardized reason catalog).

Authority: docs/06_ERD.md, PART B → ``R11 — reason_code_ref``::

    R11 — reason_code_ref
    Purpose: Standardized reasons for adjustments, transfers variance,
             returns, scrap.
    PK: id | code unique
    Important fields: code, scope (ADJUSTMENT | VARIANCE | RETURN | DAMAGE),
                      label
    Classification: R

This entity has **no entry in docs/07_DATABASE_SPEC.md** — PART B reference
tables were not ported into the physical spec; the ERD is the source of truth
for ``reason_code_ref``'s own columns (``reason_code_id`` appears as an FK
target on ``inventory_transaction``, ``stock_adjustment``,
``physical_count_line``, etc. in the spec).

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` / ``version``.
    The ERD's §0.2 Governing Design Decisions states audit fields are stored by
    *every* table, so R-class editable reference tables adopt UAC. A reason
    catalog is runtime-editable (operatable admins add / retire reason codes
    per scope), so it gets UAC. Like ``currency``, it deliberately carries **no**
    ``deleted_at`` / soft-delete: the ERD does not list a soft-delete column for
    R11, and a reference catalog is retired by discontinuing use, not soft-deleted
    (``UniversalAuditColumns`` as defined in ``database.mixins`` already carries
    no ``deleted_at``, so the mixin is used as-is).

Optimistic locking:
    ``__mapper_args__ = {"version_id_col": "version"}`` opts the model into the
    UAC ``version`` column as the SQLAlchemy row-version concurrency token (per
    ``database.mixins``'s documented opt-in mechanism — the mixin supplies the
    column, the model wires the mapper). Same pattern as ``currency``.

Naming convention:
    The column-level ``unique=True`` on ``code`` auto-named via the shared
    ``MetaData`` naming convention to ``uq_reason_code_ref_code``
    (``uq_%(table_name)s_%(column_0_name)s``). The ``scope`` bounded-set CHECK
    is named via ``database.naming.ck_index_name("reason_code_ref",
    "scope_values")``: that helper returns the bare descriptor
    ``scope_values`` (see its docstring — ``NAMING_CONVENTION["ck"]`` supplies
    the ``ck_<table>_`` prefix at compile time), rendering
    ``ck_reason_code_ref_scope_values`` verbatim.

Column-type choices (prefer existing ``database.types`` helpers over raw
``String(N)`` literals — no length invented):

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``. R-class short code per
  the helper's documented purpose ("SKU / warehouse code / currency ISO-3 /
  short codes"); a reason code (e.g. ``DAMAGED_IN_TRANSIT``, ``EXPIRED``) is a
  short controlled-vocabulary token. ``StringLength.CODE_SHORT`` (40) is the
  existing, authoritative fit.
* ``scope`` — ``state_token_type()`` → ``VARCHAR(16)``. A bounded enum token
  (longest value ``ADJUSTMENT`` / ``VARIANCE`` = 9 chars); ``STATE_TOKEN`` (16)
  is the existing helper for "short state / channel / type token" — a precise
  fit. Bounded to ``{ADJUSTMENT, VARIANCE, RETURN, DAMAGE}`` by the CHECK
  below.
* ``label`` — ``name_type()`` → ``VARCHAR(160)``. Human-readable reason
  description; ``StringLength.NAME`` is the existing helper for "human display
  name". ``description_type()`` (255) is free-text line description (too wide /
  wrong semantic); ``code_short`` (40) too tight for descriptive labels.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name
from database.types import code_short_type, name_type, state_token_type


class ReasonCodeRef(Base, UniversalAuditColumns):
    """``R11 — reason_code_ref`` — standardized reason catalog (Classification: R)."""

    __tablename__ = "reason_code_ref"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token (mixins.py opt-in mechanism).
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- code
    # R-class short controlled-vocabulary code (e.g. DAMAGED_IN_TRANSIT).
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # ---------------------------------------------------------------- scope
    # Bounded enum token {ADJUSTMENT, VARIANCE, RETURN, DAMAGE}; constrained
    # by the CHECK in __table_args__. ``state_token_type`` (VARCHAR(16)) is the
    # existing, authoritative fit for "short state / channel / type token".
    scope: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # ---------------------------------------------------------------- label
    # Human-readable reason description; ``name_type`` (VARCHAR(160)) is the
    # existing helper for human display names.
    label: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # ----------------------------------------------------------------- constraints
    __table_args__ = (
        # CHECK: bound ``scope`` to the ERD's enumerated set. Named via the
        # convention helper: ``ck_index_name`` returns the bare descriptor
        # ``scope_values`` and ``NAMING_CONVENTION["ck"]`` supplies the
        # ``ck_reason_code_ref_`` prefix at compile time, rendering
        # ``ck_reason_code_ref_scope_values`` verbatim.
        CheckConstraint(
            "scope IN ('ADJUSTMENT', 'VARIANCE', 'RETURN', 'DAMAGE')",
            name=ck_index_name("reason_code_ref", "scope_values"),
        ),
    )


__all__ = ["ReasonCodeRef"]
