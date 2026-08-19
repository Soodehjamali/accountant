"""SQLAlchemy metadata naming convention for the Enterprise ERP (SIWRMS).

Provides the shared ``NAMING_CONVENTION`` dictionary consumed by
``database/base.py`` when it constructs the project ``MetaData`` instance, plus
a small set of helpers for building the human-readable constraint and index
names the database spec mandates.

Spec-mandated prefixes (docs/07_DATABASE_SPEC.md, repeated across every table
definition):

    pk_<table>                                  primary key
    fk_<table>_<column>_<referred_table>_...    foreign key
    uq_<table>_<descriptor>                     unique constraint
    ck_<table>_<descriptor>                      check constraint
    idx_<table>_<descriptor>                     explicit operational index

The constraint *templates* below are the canonical SQLAlchemy
``MetaData.naming_convention`` placeholders; ``database/base.py`` is the only
module that instantiates a ``MetaData`` (this module deliberately does NOT — it
is naming vocabulary only). Explicit indexes use ``idx_index_name()`` since they
are authored by hand (e.g. ``idx_shipment_active``, ``idx_invoice_ar_aging``)
rather than auto-derived from a single column.

Authority:
    - docs/06_ERD.md          (~0.5 Naming Conventions)
    - docs/07_DATABASE_SPEC.md (prefix + sample-name evidence per table)
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Final

from database.constants import POSTGRES_IDENTIFIER_MAX_LENGTH

# ---------------------------------------------------------------------------
# Prefix tokens — single source of truth for the convention dictionary.
# Kept here (not in constants.py) because they are *naming-convention*
# vocabulary, not generic project-wide data, per the module split.
# ---------------------------------------------------------------------------
PRIMARY_KEY_PREFIX: Final[str] = "pk"
FOREIGN_KEY_PREFIX: Final[str] = "fk"
UNIQUE_PREFIX: Final[str] = "uq"
CHECK_PREFIX: Final[str] = "ck"
INDEX_PREFIX: Final[str] = "idx"


def _join(*parts: str) -> str:
    """Join non-empty name parts with single underscores.

    Drops empty / whitespace-only segments so callers can pass optional
    descriptors without producing doubled underscores or trailing noise:
    ``_join("uq", "shipment", "number") -> "uq_shipment_number"``.
    """

    return "_".join(p for p in (part.strip() for part in parts) if p)


def _enforce_length_limit(name: str) -> str:
    """Deterministically shorten ``name`` if it exceeds PostgreSQL's 63-char cap.

    PostgreSQL silently truncates identifiers longer than
    ``POSTGRES_IDENTIFIER_MAX_LENGTH`` (63) at the server, which means two
    distinct long names that only differ in their tail (e.g. two different
    self-referencing FKs on tables with a shared long prefix) could silently
    collide once truncated by the server — a failure mode our own naming
    helpers should prevent proactively rather than let Postgres discover.

    When ``len(name) <= POSTGRES_IDENTIFIER_MAX_LENGTH`` this is a no-op —
    every already-correct short name is returned completely unchanged (same
    input, same output), so this guard cannot perturb any name that was
    already within the limit.

    When ``name`` is too long, it is truncated and an 8-hex-char suffix
    derived from ``hashlib.sha256(name.encode("utf-8"))`` is appended, so:

    * The result is stable across runs and across
      Python versions/platforms — ``hashlib.sha256`` is specified to produce
      identical digests everywhere, unlike the builtin ``hash()``, which is
      salted per-process (``PYTHONHASHSEED``) and is NOT used here for
      exactly that reason: using it would make generated DDL
      non-reproducible across two runs of the same migration tooling.
    * Two long names that only differ in a truncated-away tail get different
      hash suffixes (barring a SHA-256 collision), so they don't silently
      collapse onto the same shortened identifier.

    Args:
        name: The fully-assembled candidate identifier (already includes any
          prefix such as ``fk_`` / ``uq_`` / ``idx_`` / ``pk_``).

    Returns:
        ``name`` unchanged if it already fits; otherwise a
        ``POSTGRES_IDENTIFIER_MAX_LENGTH``-char string consisting of a
        truncated prefix of ``name`` plus ``"_" + <8 hex chars>``.
    """

    if len(name) <= POSTGRES_IDENTIFIER_MAX_LENGTH:
        return name

    digest_suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    suffix = f"_{digest_suffix}"
    truncate_at = POSTGRES_IDENTIFIER_MAX_LENGTH - len(suffix)
    return f"{name[:truncate_at]}{suffix}"


#: SQLAlchemy ``MetaData.naming_convention`` dictionary.
#:
#: The recognised keys are: ``ix`` (index), ``uq`` (unique constraint),
#: ``ck`` (check constraint), ``fk`` (foreign key), ``pk`` (primary key).
#: Placeholder tokens render constraint defaults; for hand-authored names the
#: explicit ``name=`` passed to the constraint object always wins, so this
#: convention only governs the *unnamed* fallback path — keeping all generated
#: DDL deterministic and spec-prefixed.
#:
#: Note: SQLAlchemy's convention ``ix`` key governs *implicit* index naming;
#: the spec's hand-authored ``idx_<table>_<descriptor>`` operational indexes
#: are produced by ``idx_index_name()`` below and passed an explicit ``name``.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": f"{INDEX_PREFIX}_%(column_0_label)s",
    "uq": f"{UNIQUE_PREFIX}_%(table_name)s_%(column_0_name)s",
    "ck": f"{CHECK_PREFIX}_%(table_name)s_%(constraint_name)s",
    "fk": (
        f"{FOREIGN_KEY_PREFIX}_%(table_name)s_%(column_0_name)s"
        f"_%(referred_table_name)s_%(referred_column_0_name)s"
    ),
    "pk": f"{PRIMARY_KEY_PREFIX}_%(table_name)s",
}


def pk_index_name(table_name: str) -> str:
    """Primary-key constraint name: ``pk_<table>``.

    Mirrors the spec's ``pk_<entity>`` rule applied to every UUID PK.
    """

    return _enforce_length_limit(_join(PRIMARY_KEY_PREFIX, table_name))


def fk_index_name(
    table_name: str,
    column_name: str,
    referred_table_name: str,
    referred_column_name: str = "id",
) -> str:
    """Foreign-key constraint name, mirroring spec convention.

    e.g. ``fk_shipment_order_id_order_id``. Stays deterministic for migrations
    and reproducible across environments.

    Long combinations (e.g. a self-referencing FK on an already-long table
    name, such as ``inventory_transaction.reversal_of_id`` ->
    ``inventory_transaction.id``) can exceed PostgreSQL's 63-char identifier
    limit; ``_enforce_length_limit`` deterministically shortens the result
    when that happens (see its docstring), so the name returned here is
    always PostgreSQL-safe.
    """

    return _enforce_length_limit(
        _join(
            FOREIGN_KEY_PREFIX,
            table_name,
            column_name,
            referred_table_name,
            referred_column_name,
        )
    )


def uq_index_name(table_name: str, descriptor: str) -> str:
    """Unique-constraint name: ``uq_<table>_<descriptor>``.

    The spec attaches *one descriptor* per unique constraint — either a single
    column-name (``uq_shipment_number``) or a multi-column / partial descriptor
    (``uq_commission_transaction_seq``). Callers supply the descriptor token so
    the human-readable suffix matches the approved spec names exactly.
    """

    return _enforce_length_limit(_join(UNIQUE_PREFIX, table_name, descriptor))


def ck_index_name(table_name: str, descriptor: str) -> str:
    """Bare check-constraint descriptor to pass to ``CheckConstraint(name=)``.

    Returns *only* the ``<descriptor>`` token — **not** the full
    ``ck_<table>_<descriptor>`` name — because ``NAMING_CONVENTION["ck"]`` is
    ``ck_%(table_name)s_%(constraint_name)s``: at compile time SQLAlchemy
    substitutes ``%(constraint_name)s`` with the explicit ``name=`` passed to
    ``CheckConstraint`` and supplies the ``ck_<table>_`` prefix itself. Passing
    the full name here would render double-wrapped, e.g. for
    ``ck_index_name("shipment", "state")`` the convention would emit
    ``ck_shipment_ck_shipment_state``.

    Usage::

        from database.naming import ck_index_name
        CheckConstraint("state IN ('A','B')", name=ck_index_name("shipment", "state"))
        # -> compiled constraint name: ck_shipment_state  (matches spec verbatim)

    The produced DDL therefore reads ``ck_<table>_<descriptor>`` exactly as in
    docs/07_DATABASE_SPEC.md.

    Args:
        table_name: Table the constraint belongs to. **Accepted for the public
          signature's consistency with the sibling helpers and for
          documentation**, but deliberately NOT folded into the returned token
          — the convention supplies it. Keeping it a parameter preserves a stable
          call shape across all ``*_index_name`` helpers.
        descriptor: Short descriptor for the constraint (e.g. ``"state"``,
          ``"unit_price_nonneg"``).

    Returns:
        The bare descriptor ``<descriptor>``.

    KNOWN GAP — deliberately NOT given the ``_enforce_length_limit`` guard:
        This function returns only the bare descriptor, never the full
        ``ck_<table>_<descriptor>`` name — that name is assembled later by
        SQLAlchemy itself via ``NAMING_CONVENTION["ck"]`` dict substitution
        at constraint-compile time, not by Python code in this module (see
        the docstring above). Because the 63-char risk applies to the
        *final compiled* name, not to the bare token returned here, this
        helper cannot enforce the limit — there is nothing here yet long
        enough to check, and by the time the full name exists, it is
        SQLAlchemy's own convention machinery producing it, past this
        function's return.

        This is a **known, unresolved follow-up gap**, not something this
        change silently fixes: the same 63-char risk still exists for (a)
        CHECK constraints on long ``table_name`` + ``descriptor`` combinations
        once ``ck_<table>_<descriptor>`` is assembled by SQLAlchemy, and (b)
        the ``uq`` / ``fk`` (and ``pk`` / ``ix``) entries in
        ``NAMING_CONVENTION`` themselves, for their *implicit* fallback path
        — i.e. any constraint left with ``name=None`` that SQLAlchemy names
        automatically from the convention dict's ``%(...)s`` templates,
        rather than via one of this module's own ``*_index_name`` helpers.
        Every helper in this module (``fk_index_name``, ``uq_index_name``,
        ``idx_index_name``, ``pk_index_name``) is guarded because they fully
        assemble a name in Python before SQLAlchemy sees it; the
        convention-dict path and this function's bare-token path both still
        need a separate fix (e.g. a custom naming-convention callable) and
        are explicitly out of scope for this change.
    """

    return descriptor


def idx_index_name(table_name: str, descriptor: str) -> str:
    """Explicit operational index name: ``idx_<table>_<descriptor>``.

    For the hand-authored, purpose-named indexes the spec calls out (partial /
    recommended / composite indexes) — e.g. ``idx_shipment_active``,
    ``idx_invoice_ar_aging``, ``idx_inventory_transaction_unreversed``.
    Passed an explicit ``name=`` on the ``Index`` object.
    """

    return _enforce_length_limit(_join(INDEX_PREFIX, table_name, descriptor))


def composite_descriptor(columns: Sequence[str]) -> str:
    """Collapse a column sequence into a single descriptor token.

    For unique / index constraints spanning multiple columns the spec joins
    column names with underscores: ``(product_id, lot_id) -> "product_id_lot_id"`.
    Empty input yields ``""`` so callers may safely chain into ``_join``.
    """

    return "_".join(c for c in columns if c)


__all__ = [
    "CHECK_PREFIX",
    "FOREIGN_KEY_PREFIX",
    "INDEX_PREFIX",
    "NAMING_CONVENTION",
    "PRIMARY_KEY_PREFIX",
    "UNIQUE_PREFIX",
    "ck_index_name",
    "composite_descriptor",
    "fk_index_name",
    "idx_index_name",
    "pk_index_name",
    "uq_index_name",
]
