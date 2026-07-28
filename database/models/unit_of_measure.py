"""``R2 — unit_of_measure`` ORM model (UoM definitions and conversions).

Authority: docs/06_ERD.md, PART B → ``R2 — unit_of_measure``::

    R2 — unit_of_measure
    Purpose: UoM definitions and conversions (BR & BRF §1.5).
    PK: id
    Important fields: code (unique, e.g. BOX, PALLET, PCS), name, class (BASE/DERIVED)
    Unique: code
    Classification: R

This entity has **no entry in docs/07_DATABASE_SPEC.md** — PART B reference
tables were not ported into the physical spec; the ERD is the source of truth
for ``unit_of_measure``'s own columns (``uom_id`` / ``unit_of_measure_id``
appears as an FK target on ``product``, ``uom_conversion``, and the various
``*_uom_id`` quantity columns in the spec).

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` / ``version``.
    The ERD's §0.2 Governing Design Decisions states audit fields are stored by
    *every* table, so R-class editable reference tables adopt UAC. A UoM
    catalog is runtime-editable (operators add/retire units of measure and their
    conversion factors), so it gets UAC. Like ``currency`` /
    ``reason_code_ref`` / ``movement_type_ref`` / ``city_ref``, it deliberately
    carries **no** ``deleted_at`` / soft-delete: the ERD does not list a
    soft-delete column for R2, and a reference catalog is retired by
    discontinuing use, not soft-deleted (``UniversalAuditColumns`` as defined in
    ``database.mixins`` already carries no ``deleted_at``, so the mixin is used
    as-is).

Optimistic locking:
    ``__mapper_args__ = {"version_id_col": "version"}`` opts the model into the
    UAC ``version`` column as the SQLAlchemy row-version concurrency token (per
    ``database.mixins``'s documented opt-in mechanism — the mixin supplies the
    column, the model wires the mapper). Same pattern as the other R-class
    reference models.

Naming convention:
    The column-level ``unique=True`` on ``code`` auto-named via the shared
    ``MetaData`` naming convention to ``uq_unit_of_measure_code``
    (``uq_%(table_name)s_%(column_0_name)s``). The ``class`` bounded-set CHECK
    is named via ``database.naming.ck_index_name("unit_of_measure",
    "class_values")``: that helper returns the bare descriptor ``class_values``
    (see its docstring — ``NAMING_CONVENTION["ck"]`` supplies the
    ``ck_<table>_`` prefix at compile time), rendering
    ``ck_unit_of_measure_class_values`` verbatim.

The ``class`` Python-keyword clash:
    The ERD column is named ``class`` with the bounded set ``BASE`` /
    ``DERIVED``. ``class`` is a Python reserved keyword, so a model attribute
    literally named ``class`` would be a ``SyntaxError`` at import time
    (``class: Mapped[str] = ...`` is parsed as the start of a class body, not
    an attribute binding). The resolution follows SQLAlchemy's documented
    escaping idiom for keyword-clash column names: the Python attribute is
    named ``class_`` (trailing underscore — the PEP 8 convention for a name
    colliding with a keyword) and ``mapped_column`` is given the DB column name
    explicitly via its first positional argument ``"class"``
    (equivalently ``mapped_column(name="class", ...)``). SQLAlchemy then maps
    the ``class_`` attribute to a column literally named ``class`` in the
    generated DDL: ``class_`` is the ORM-facing handle, ``class`` is the only
    name that ever reaches the database. The CHECK in ``__table_args__``
    references the SQL column name ``class`` (not the Python attribute), as
    does the naming-convention ``%(table_name)s`` / ``%(column_0_name)s``
    expansion for the ``uq_unit_of_measure_code`` unique constraint.

Column-type choices (prefer existing ``database.types`` helpers over raw
``String(N)`` literals — no length invented):

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``. A UoM code is a short
  controlled token (e.g. ``BOX``, ``PALLET``, ``PCS`` per the ERD); the helper's
  documented purpose is "SKU / warehouse code / currency ISO-3 / short codes".
  ``StringLength.CODE_SHORT`` (40) is the existing, non-invented, authoritative
  fit. Same choice as ``reason_code_ref.code`` / ``city_ref.code``.
* ``name`` — ``name_type()`` → ``VARCHAR(160)``. Human-readable unit name
  (e.g. "Box", "Pallet", "Pieces"); ``StringLength.NAME`` is the existing helper
  for "human display names". Same choice as ``city_ref.name``.
* ``class`` — ``state_token_type()`` → ``VARCHAR(16)``. A bounded enum token
  (longest value ``DERIVED`` = 7 chars); ``STATE_TOKEN`` (16) is the existing
  helper for "short state / channel / type token" — a precise fit. Bounded to
  ``{BASE, DERIVED}`` by the CHECK below. Same choice as
  ``reason_code_ref.scope``.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name
from database.types import code_short_type, name_type, state_token_type


class UnitOfMeasure(Base, UniversalAuditColumns):
    """``R2 — unit_of_measure`` — UoM definitions catalog (Classification: R)."""

    __tablename__ = "unit_of_measure"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token (mixins.py opt-in mechanism).
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- code
    # Short UoM controlled-vocabulary code (e.g. BOX, PALLET, PCS per the ERD).
    # ``code_short_type`` (VARCHAR(40)) is the existing, non-invented helper
    # whose documented purpose is short codes.
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # ---------------------------------------------------------------- class
    # ``class`` is a PYTHON KEYWORD — a model attribute literally named ``class``
    # would be a ``SyntaxError`` (``class: Mapped[...]`` is parsed as a class
    # body, not an attribute). Resolution (SQLAlchemy's documented escape for
    # keyword-clash column names): the ORM-facing Python attribute is ``class_``
    # (PEP 8 trailing-underscore convention), and the DB column name is supplied
    # explicitly as ``mapped_column``'s first positional argument ``"class"``
    # (equivalently ``name="class"``). The attribute ``class_`` therefore maps
    # to a column LITERALLY named ``class`` in the generated DDL — ``class_``
    # is the Python handle only; ``class`` is the only name the DB ever sees
    # (the CHECK below and the naming-convention expansion both use ``class``).
    class_: Mapped[str] = mapped_column(
        "class",
        state_token_type(),
        nullable=False,
    )

    # ----------------------------------------------------------------- name
    # Human-readable unit name (e.g. "Box", "Pallet", "Pieces"); ``name_type``
    # (VARCHAR(160)) is the existing helper for human display names.
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # ----------------------------------------------------------------- constraints
    __table_args__ = (
        # CHECK: bound ``class`` to the ERD's enumerated set {BASE, DERIVED}.
        # Named via the convention helper: ``ck_index_name`` returns the bare
        # descriptor ``class_values`` and ``NAMING_CONVENTION["ck"]`` supplies
        # the ``ck_unit_of_measure_`` prefix at compile time, rendering
        # ``ck_unit_of_measure_class_values`` verbatim. The SQL expression
        # references the DB column name ``class`` (not the Python attribute
        # ``class_``).
        CheckConstraint(
            "class IN ('BASE', 'DERIVED')",
            name=ck_index_name("unit_of_measure", "class_values"),
        ),
    )


__all__ = ["UnitOfMeasure"]
