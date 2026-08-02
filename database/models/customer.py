"""``M8 — customer`` ORM model (customer master, its own Aggregate Root).

Authority: ``06_ERD.md``, PART C → ``M8 — customer``::

    M8 — customer (its OWN Aggregate Root — per correction #2)
    Purpose: Customer master, independent of rep (SRS E13). Contains
             credit limit, locality for Scenario A/B.
    PK: id
    FK: city_ref_id → city_ref
    Important fields: code (unique), name, type (CustomerType),
                      billing_address, city_ref_id, credit_limit_amount,
                      currency_id → currency, status, tax_number
    Unique: code
    Business constraints: cannot hard-delete (historical invoices
                          preserved); credit-limit violations block new
                          order submission
    Classification: M + soft-deletable

Same gap as every other table with no dedicated spec section so far:
``06_ERD.md`` is M8's sole authority — M8 has no detailed section in
``07_DATABASE_SPEC.md``.

Enum, ``06_ERD.md`` PART A::

    CustomerType: INDIVIDUAL, CORPORATE

"its OWN Aggregate Root — per correction #2":
    This parenthetical is not incidental ERD phrasing — it's an explicit
    design correction, and it's the *only* entity in the ERD's PART C
    carrying this specific annotation. It ties directly to ``CLAUDE.md``'s
    standalone project rule "Customer is an Aggregate Root." — i.e.
    ``customer`` is independent of ``representative`` (the ERD's own
    "independent of rep" phrase in M8's Purpose line), not a child entity
    owned by or cascading from any other aggregate. This model does not
    encode aggregate-boundary behavior in SQL (that's a DDD/service-layer
    concept, not a schema constraint), but the citation is recorded here
    because the correction shaped which FK exists on this table
    (``city_ref_id``, a plain lookup reference) versus which FKs point *to*
    it from elsewhere (``representative`` never owns ``customer`` —
    ``customer_rep_assignment`` (C6) is the join, not a parent/child FK on
    this table).

``city_ref_id`` — nullable FK:
    ``city_ref_id → city_ref`` (R13). The ERD does not mark it required,
    so it is nullable — the same treatment ``warehouse.city_ref_id`` and
    ``representative.home_city_ref_id`` already give their own ``city_ref``
    FKs.

``type`` — explicit ERD vocabulary, not an assumption:
    Bounded to ``CustomerType`` (PART A): ``INDIVIDUAL`` / ``CORPORATE``.
    Like ``representative.status`` / ``commission_config.order_type``, this
    vocabulary is given directly in the ERD text, not assumed the way
    ``carrier.py`` / ``warehouse.py`` / ``app_user.py`` had to assume their
    own unspecified ``status`` vocabularies.

``billing_address`` — no dedicated type exists yet:
    The ERD lists a bare ``billing_address`` field with no further shape.
    ``description_type()`` (``VARCHAR(255)``) is used as the closest
    existing factory — the same placeholder treatment ``warehouse.py``
    gives its own ``address`` field, not a considered structured-address
    representation.

``credit_limit_amount`` — type AND default choice:
    ``money_type()`` → ``NUMERIC(18, 4)``, matching the factory's own
    "money totals" family. The ERD lists this as a core field ("Contains
    credit limit") but does not state a default. This model adds an
    explicit default of ``0`` (``Numeric`` zero, via both Python-side
    ``default`` and ``server_default``): a freshly created customer with no
    credit limit configured yet defaults to no credit extended (0) rather
    than requiring every insert to supply a value or leaving the column
    without a default and forcing every caller to reason about what "no
    limit set yet" means. This is a stated choice, not an ERD-given value —
    flagged explicitly here, the same way ``product_lot``'s absence of
    soft-delete and other model-level judgment calls are flagged rather
    than silently made.

``currency_id`` — required FK:
    ``currency_id → currency`` (R5), ``NOT NULL`` — the ERD's ``Important
    fields`` line does not parenthesize this one as nullable (unlike
    ``city_ref_id``, which the ERD marks as this table's sole declared FK
    without a nullability qualifier either, but which follows this
    codebase's established "no explicit requirement stated → nullable"
    convention for lookup-reference FKs — see the note above). ``currency``
    (R5) already exists in this codebase (``database/models/currency.py``),
    so this is a real ``ForeignKey()``, not a deferred one.

``status`` — ASSUMPTION, not a fact taken from the ERD:
    PART A does **not** list a dedicated ``CustomerStatus`` enum vocabulary
    — unlike ``CustomerType``, which is explicit. This model assumes
    ``ACTIVE`` / ``INACTIVE``, mirroring ``carrier.py`` / ``warehouse.py``
    / ``app_user.py``'s identical treatment of their own unspecified
    ``status`` fields. This is an assumption made to fill an unspecified
    vocabulary, not a value taken from the ERD text itself.

``tax_number`` — nullable:
    The ERD does not mark it required, and states no uniqueness for it
    (the ERD's own ``Unique:`` line names only ``code``) — nullable,
    ``code_short_type()``, no uniqueness constraint, mirroring the same
    treatment ``representative.py`` gives its own ``national_id`` /
    ``tax_id`` sibling identifier fields.

Business constraints:
    "cannot hard-delete (historical invoices preserved)" is not a separate,
    unimplemented rule — it is exactly what this model's ``deleted_at`` /
    soft-delete column already provides (rows are marked deleted, never
    physically removed, so historical FK references from ``invoice`` and
    similar tables stay resolvable). The ERD's own classification —
    "M + soft-deletable" — and this specific business constraint reinforce
    each other; they are cited together here rather than treated as two
    independent facts. "credit-limit violations block new order submission"
    is a genuine cross-table/temporal rule (it depends on aggregate
    order/invoice state at the moment a new order is submitted, not on any
    value fixed within this row alone) — the same treatment every other
    cross-table/temporal rule has received so far (e.g. ``warehouse.py``'s
    "cannot deactivate a warehouse holding non-zero stock"). It is
    documented here, not encoded as a SQL CHECK; enforcement is
    service-layer only, at order-submission time.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Customer`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking (``__mapper_args__ = {"version_id_col":
    "version"}``), exactly like every other model in this codebase so far.

Soft delete:
    Classification is "M + soft-deletable" — same as ``product`` (M1),
    ``warehouse`` (M4), ``app_user`` (M10). Following their precedent, no
    reusable soft-delete mixin is relied on; ``deleted_at`` is declared
    directly as a nullable timezone-aware ``TIMESTAMPTZ``, default ``NULL``
    meaning not deleted. See the business-constraints note above for why
    this is not merely a stylistic match to the other M-tables but the
    actual mechanism satisfying M8's own stated "cannot hard-delete" rule.

Naming convention:
    ``code`` uses column-level ``unique=True`` → ``uq_customer_code``,
    mirroring ``warehouse.code`` / ``representative.code``. ``city_ref_id``
    / ``currency_id`` use ``fk_index_name`` → ``fk_customer_city_ref_id_city_ref_id``
    / ``fk_customer_currency_id_currency_id`` (the trailing ``_id`` is
    ``fk_index_name``'s default ``referred_column_name="id"``, appended
    after the referred table name — verified below by actually calling
    ``fk_index_name``, not assumed from the table name alone). The ``type``
    vocabulary is bounded by a CHECK named via ``ck_index_name`` →
    ``ck_customer_type_values``; ``status`` likewise →
    ``ck_customer_status_values``.

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)``.
* ``name`` — ``name_type()`` → ``VARCHAR(160)``.
* ``type`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``INDIVIDUAL`` / ``CORPORATE`` (explicit ERD vocabulary — see note
  above).
* ``billing_address`` — ``description_type()`` → ``VARCHAR(255)`` (see
  note above).
* ``credit_limit_amount`` — ``money_type()`` → ``NUMERIC(18, 4)``, default
  ``0`` (stated choice — see note above).
* ``status`` — ``state_token_type()`` → ``VARCHAR(16)``, constrained to
  ``ACTIVE`` / ``INACTIVE`` (assumption — see note above).
* ``tax_number`` — ``code_short_type()`` → ``VARCHAR(40)``.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import code_short_type, description_type, money_type, name_type, state_token_type


class Customer(Base, UniversalAuditColumns):
    """``M8 — customer`` — customer master, its own Aggregate Root (Classification: M + soft-deletable)."""

    __tablename__ = "customer"

    # Optimistic locking — activate the UAC ``version`` column as the
    # SQLAlchemy row-version concurrency token.
    __mapper_args__ = {"version_id_col": "version"}

    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ city_ref_id
    # ERD does not mark this required — nullable, mirrors
    # warehouse.city_ref_id / representative.home_city_ref_id.
    city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "city_ref.id",
            name=fk_index_name("customer", "city_ref_id", "city_ref"),
        ),
        nullable=True,
    )

    # -------------------------------------------------------------- code
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # -------------------------------------------------------------- name
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- type
    # Explicit ERD vocabulary (PART A CustomerType): INDIVIDUAL / CORPORATE.
    type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------- billing_address
    # No dedicated address type exists yet — description_type() is the
    # closest existing factory (see module docstring note above).
    billing_address: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )

    # ----------------------------------------------------- credit_limit_amount
    # Default 0 is a stated choice, not an ERD-given value (see module
    # docstring note above).
    credit_limit_amount: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ------------------------------------------------------------ currency_id
    # NOT NULL — currency (R5) already exists, so this is a real FK, not
    # deferred.
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("customer", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- status
    # ASSUMPTION: no dedicated CustomerStatus vocabulary is given in PART A.
    # Mirrors carrier.py/warehouse.py/app_user.py's status pattern:
    # ACTIVE/INACTIVE.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # -------------------------------------------------------------- tax_number
    # ERD does not mark this required or unique.
    tax_number: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )

    # -------------------------------------------------------------- deleted_at
    # Direct, opt-in soft-delete marker (same pattern as product.py /
    # warehouse.py / app_user.py); NULL means not soft-deleted. This is the
    # mechanism behind M8's own "cannot hard-delete" business constraint
    # (see module docstring).
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('INDIVIDUAL', 'CORPORATE')",
            name=ck_index_name("customer", "type_values"),
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name=ck_index_name("customer", "status_values"),
        ),
    )


__all__ = ["Customer"]
