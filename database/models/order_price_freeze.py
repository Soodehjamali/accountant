"""``H6 (ERD: T13) — order_price_freeze`` ORM model (price-resolution audit record, optional per ERD).

Authority: ``07_DATABASE_SPEC.md`` §H6 (labeled ``H6 (ERD: T13) —
order_price_freeze (optional)`` in the spec's own section header) -- this
table **does** have a full detailed spec section, so the spec is primary
authority here; ``06_ERD.md`` (F.4 — Sales / Order, T13 line, marked
"optional (or embed in order_line)") is secondary/corroborating only::

    H6 (ERD: T13) — order_price_freeze (optional)
    Purpose: Explicit record of the price-resolution precedence chain used
        for an order line -- supports audit/disputes beyond what
        order_line.price_history_id alone shows.
    PK: id (UUID)
    FK: order_line_id -> order_line.id; price_history_id -> price_history.id
    Column Definitions: +AAC; order_line_id UUID NOT NULL; resolved_price
        NUMERIC(18,4) NOT NULL; precedence_chain_json JSONB NOT NULL
        (ordered list of candidate price sources considered and why each was
        accepted/rejected); price_history_id UUID NOT NULL (the winning
        source)
    Unique: uq_order_price_freeze_line (order_line_id) -- 1:1
    Check: ck_order_price_freeze_resolved_nonneg (resolved_price >= 0)
    Business constraints: Append-only, written once at price-resolution time
        and never modified thereafter.
    Recommended Indexes: none beyond unique constraint
    Composite Indexes: none
    Partial Indexes: none
    Partitioning Strategy: None -- optional table, expected low-to-moderate
        volume
    Soft Delete Strategy: None
    Audit Strategy: created_by (AAC)
    Notes: ERD marks this table optional ("or embed in order_line"). The
        spec documents it as a standalone table per the ERD's primary
        option, since precedence_chain_json is naturally variable-shaped
        data that would otherwise bloat order_line with a JSONB column on
        the hottest write-path table in the schema -- keeping it separate
        is the spec's recommended choice.

ERD-optional table -- built here as a standalone table, per the spec's own
resolved decision:
    The ERD itself leaves this table optional ("or embed in order_line"),
    but ``07_DATABASE_SPEC.md`` §15's own Notes resolves that ambiguity in
    favor of the standalone-table option and gives a concrete rationale
    (keeping a variable-shaped JSONB column off the hottest write path,
    ``order_line``). This model follows the spec's resolved decision, not
    the ERD's still-open one -- flagged explicitly per this task's
    instruction to document which side of an ERD-vs-spec ambiguity was
    taken. No embed-in-``order_line`` variant is implemented.

Not owned by any of the three existing aggregate roots (``StockTransfer``,
``Shipment``) touched by prior changes in this codebase; this table's own
natural parent is ``order_line`` (1:1 via the unique constraint below), the
same way ``customer_ledger`` (M13) is 1:1 with ``customer`` without being
described as an "aggregate root" of its own in the ERD's aggregate-boundary
list.

Non-reserved-word FK targets -- ``order_line_id -> order_line.id`` /
``price_history_id -> price_history.id``:
    Both are ordinary identifiers, no quoting concerns for either FK.

CRITICAL naming trap -- ``order_line_id``'s 1:1 unique constraint:
    The spec's literal constraint name is ``uq_order_price_freeze_line`` --
    **not** ``uq_order_price_freeze_order_line_id``, which is what
    column-level ``unique=True`` on ``order_line_id`` would produce via
    ``NAMING_CONVENTION["uq"]`` (``uq_%(table_name)s_%(column_0_name)s``).
    This model instead uses an **explicit**
    ``UniqueConstraint("order_line_id", name=uq_index_name(
    "order_price_freeze", "line"))``, passing the helper a bare descriptor of
    ``"line"`` (not ``"order_line_id"``) so ``uq_index_name`` assembles
    ``uq_`` + ``order_price_freeze`` + ``line`` ->
    ``uq_order_price_freeze_line`` exactly. This is the standard
    "supply a short descriptor to the normal helper" treatment (same
    mechanism as ``stock_transfer.py``'s ``uq_stock_transfer_number``), NOT
    the bare-literal-string override treatment ``shipment_line.py`` /
    ``transfer_line.py`` use for their own composite naming traps -- flagged
    so the distinction between the two naming-trap resolutions stays clear
    for future models.

``precedence_chain_json`` -- ``JSONB``, second JSONB column in this codebase:
    ``database/models/report_definition.py``'s own ``parameters`` column is
    this codebase's first JSONB column (per that model's own module
    docstring); this is the second. Declared via
    ``sqlalchemy.dialects.postgresql.JSONB`` directly (the same import this
    codebase's first JSONB consumer already established), since
    ``database/types.py`` has no JSONB factory (its scope is limited to
    ``NumericPrecision`` / ``StringLength`` members per that module's own
    docstring). Typed as ``Mapped[list[dict[str, Any]]]`` -- the spec's own
    description ("ordered list of candidate price sources considered and
    why each was accepted/rejected") names a JSON *array* of per-source
    records, not a JSON object, so the Python-side annotation reflects that
    shape precisely rather than defaulting to a generic ``dict``.

``resolved_price`` -- ``money_type()``, exact spec match:
    ``NUMERIC(18, 4)`` per spec -- an ordinary money column, no placeholder
    needed.

No ``deleted_at`` -- explicit per spec:
    Spec §12: *"None"* -- unlike ``shipment`` / ``stock_transfer`` (both
    "Supported"), this table has no soft-delete column. Consistent with its
    append-only nature: a row written once and never modified has nothing
    for a soft-delete flag to mark as withdrawn.

Naming convention:
    ``order_line_id``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("order_price_freeze", "line")``, NOT
    column-level ``unique=True``. The CHECK uses ``ck_index_name`` normally:
    the standard helper output already matches the spec's literal name
    verbatim (``ck_order_price_freeze_resolved_nonneg``) -- no override
    needed. Both FKs use ``fk_index_name`` normally.

Out of scope for this model (not implemented here):
    * Any Alembic migration.

Audit-column family -- ``AppendOnlyAuditColumns`` (AAC), NOT UAC:
    The spec's own §4 Column Definitions table opens with ``+AAC``
    (*"Append-only audit columns"*), and §7 Business Constraints states
    plainly *"Append-only, written once at price-resolution time and never
    modified thereafter"* -- an unambiguous, spec-declared append-only
    table, unlike ``shipment`` / ``shipment_line`` / ``invoice`` /
    ``invoice_line`` (all spec'd ``+UAC``). ``OrderPriceFreeze`` therefore
    gets ``created_at`` / ``created_by`` only -- no ``updated_at`` /
    ``updated_by`` / ``version``, and consequently no ``__mapper_args__ =
    {"version_id_col": ...}``.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import AppendOnlyAuditColumns
from database.naming import ck_index_name, fk_index_name, uq_index_name
from database.types import money_type


class OrderPriceFreeze(Base, AppendOnlyAuditColumns):
    """``H6 (ERD: T13) — order_price_freeze`` — price-resolution precedence audit record (Classification: H)."""

    __tablename__ = "order_price_freeze"

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ----------------------------------------------------------- order_line_id
    # Unique via an explicit UniqueConstraint below (1:1 with order_line) --
    # NOT column-level unique=True. See the module docstring's "CRITICAL
    # naming trap" note.
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "order_line.id",
            name=fk_index_name("order_price_freeze", "order_line_id", "order_line"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- resolved_price
    resolved_price: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
    )

    # --------------------------------------------------------- precedence_chain_json
    # Second JSONB column in this codebase -- see module docstring's
    # dedicated section. Ordered list of candidate-price-source records.
    precedence_chain_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(),
        nullable=False,
    )

    # ----------------------------------------------------------- price_history_id
    price_history_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "price_history.id",
            name=fk_index_name("order_price_freeze", "price_history_id", "price_history"),
        ),
        nullable=False,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "line" (not "order_line_id") so the assembled name
        # is uq_order_price_freeze_line, not the longer
        # uq_order_price_freeze_order_line_id that column-level
        # unique=True's implicit convention would produce.
        UniqueConstraint(
            "order_line_id",
            name=uq_index_name("order_price_freeze", "line"),
        ),
        # CHECK: resolved_price non-negative.
        CheckConstraint(
            "resolved_price >= 0",
            name=ck_index_name("order_price_freeze", "resolved_nonneg"),
        ),
    )


__all__ = ["OrderPriceFreeze"]
