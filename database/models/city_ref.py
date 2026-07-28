"""``R13 — city_ref`` ORM model (canonical city/locality reference).

Authority: docs/06_ERD.md, PART B → ``R13 — city_ref``::

    R13 — city_ref
    Purpose: Canonical city/locality reference used for Scenario A vs B
             determination.
    PK: id | code unique
    Important fields: name, region, country
    Classification: R

This entity has **no entry in docs/07_DATABASE_SPEC.md** — PART B reference
tables were not ported into the physical spec; the ERD is the source of truth
for ``city_ref``'s own columns (``city_ref_id`` appears as an FK target on
``warehouse``, ``representative``, ``customer``, and as ``customer_city_ref_id`` /
``rep_city_ref_id`` snapshot columns on ``order`` in the spec).

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` / ``version``.
    The ERD's §0.2 Governing Design Decisions states audit fields are stored by
    *every* table, so R-class editable reference tables adopt UAC. A
    city/locality catalog is reference data an operator may extend at runtime,
    so it gets UAC. Like ``currency`` / ``reason_code_ref`` /
    ``movement_type_ref``, it deliberately carries **no** ``deleted_at`` /
    soft-delete: the ERD does not list a soft-delete column for R13, and a
    reference catalog is retired by discontinuing use, not soft-deleted
    (``UniversalAuditColumns`` as defined in ``database.mixins`` already carries
    no ``deleted_at``, so the mixin is used as-is).

Optimistic locking:
    ``__mapper_args__ = {"version_id_col": "version"}`` opts the model into the
    UAC ``version`` column as the SQLAlchemy row-version concurrency token (per
    ``database.mixins``'s documented opt-in mechanism — the mixin supplies the
    column, the model wires the mapper). Same pattern as the other R-class
    reference models.

Naming convention:
    The column-level ``unique=True`` on ``code`` auto-named via the shared
    ``MetaData`` naming convention to ``uq_city_ref_code``
    (``uq_%(table_name)s_%(column_0_name)s``). No CHECK constraints — the ERD
    lists no bounded set for ``region`` or ``country`` (they are canonical
    free-text references), so none is invented.

Column-type choices (prefer existing ``database.types`` helpers over raw
``String(N)`` literals — no length invented):

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``. A city/locality code is a
  short controlled token (UN/LOCODE is 5 chars; in-region municipality / postal
  prefixes fit in far less). ``StringLength.CODE_SHORT`` (40) is the existing,
  non-invented helper whose documented purpose is "SKU / warehouse code /
  currency ISO-3 / short codes" — a precise fit for a reference code.
* ``name`` — ``name_type()`` → ``VARCHAR(160)``. Canonical human-readable city
  name; ``StringLength.NAME`` is the existing helper for "human display names".
  Same choice as ``reason_code_ref.label`` / ``movement_type_ref.label``.
* ``region`` — raw ``String(80)``. **No existing ``StringLength`` member is a
  good fit.** ``STATE_TOKEN`` (16) and ``STATE_TOKEN_LONG`` (24) document
  *bounded enum tokens*, but a region / province name is free text (real
  province names can exceed 16-24 chars, e.g. "Sistan and Baluchestan" = 23,
  "Chaharmahal and Bakhtiari" = 24 — at/over 24). ``TYPE_TOKEN`` / ``CODE_SHORT``
  (40) document a *code* token, not a name, and 24-40 is borderline-tight for
  longer multi-word provinces. ``NAME`` (160) over-provisions a column whose
  sibling ``country`` we want to keep comparably compact. Per the disciplined
  rule (do not invent a new ``StringLength`` member without being asked), and
  following the same latitude as ``currency.symbol``'s justified raw literal,
  this uses a raw ``String(80)`` — enough headroom for any real province /
  region name without over-provisioning. A comment explains the gap at the
  call site.
* ``country`` — raw ``String(64)``. **No existing ``StringLength`` member is a
  good fit.** ``STATE_TOKEN`` / ``STATE_TOKEN_LONG`` are bounded enum tokens
  (wrong semantic for a country name); ``TYPE_TOKEN`` / ``CODE_SHORT`` (40) are
  *code* tokens, not names, and are borderline-tight for the longest ISO 3166
  country *long-form* name (e.g. "Congo, Democratic Republic of the" = 33;
  "Antigua and Barbuda" = 19; longest official long-form ~ 50). To comfortably
  fit every ISO long-form country name without over-provisioning toward
  ``NAME`` (160), and without inventing a new ``StringLength`` member, this
  uses a raw ``String(64)``. A comment explains the gap at the call site.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import code_short_type, name_type


class CityRef(Base, UniversalAuditColumns):
    """``R13 — city_ref`` — canonical city/locality reference (Classification: R)."""

    __tablename__ = "city_ref"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token (mixins.py opt-in mechanism).
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------------- code
    # Short controlled city/locality code (e.g. a UN/LOCODE or in-region
    # municipality code). ``code_short_type`` (VARCHAR(40)) is the existing,
    # non-invented helper whose documented purpose is short codes.
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # ----------------------------------------------------------------- name
    # Canonical human-readable city name; ``name_type`` (VARCHAR(160)) is the
    # existing helper for human display names.
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # --------------------------------------------------------------- region
    # Free-text province / region name. No existing ``StringLength`` member is
    # a good fit: ``STATE_TOKEN``/``STATE_TOKEN_LONG`` are bounded enum tokens
    # (wrong semantic), ``TYPE_TOKEN``/``CODE_SHORT`` are codes (and borderline-
    # tight at 24-40 for longer provinces), and ``NAME`` (160) over-provisions.
    # Deferred creation of a ``REGION``/``COUNTRY`` member; raw ``String(80)``
    # is the disciplined fallback (not an invented member).
    region: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    # -------------------------------------------------------------- country
    # Free-text country name (ISO 3166 short/long-form). No existing
    # ``StringLength`` member is a good fit (same gap as ``region``); raw
    # ``String(64)`` comfortably holds every ISO long-form country name without
    # over-provisioning toward ``NAME`` (160). Disciplined fallback — not an
    # invented member.
    country: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # No CHECK constraints: the ERD lists no bounded set for ``region`` /
    # ``country`` (canonical free-text references); none invented.


__all__ = ["CityRef"]
