"""``M8 — customer`` ORM model (independent customer master).

Authority: ``06_ERD.md``, M8 → ``customer``::

    M8 — customer (its OWN Aggregate Root — per correction #2)
    Purpose: Customer master, independent of rep (SRS E13). Contains credit
             limit, locality for Scenario A/B.
    PK: id
    FK: city_ref_id → city_ref
    Important fields: code (unique), name, type (CustomerType),
                      billing_address, city_ref_id, credit_limit_amount,
                      currency_id → currency, status, tax_number
    Unique: code
    Business constraints: cannot hard-delete (historical invoices preserved);
                          credit-limit violations block new order submission
    Classification: M + soft-deletable

``06_ERD.md`` is M8's sole authority: M8 has no detailed section in
``07_DATABASE_SPEC.md``. The ERD explicitly corrects the domain model by
making Customer its **own Aggregate Root**, with its own identity and table,
rather than nesting it under Representative. This follows ``CLAUDE.md``'s
"Customer is an Aggregate Root" rule.

PART A defines ``CustomerType`` as exactly ``INDIVIDUAL`` / ``CORPORATE``;
the ``type`` CHECK below transcribes that vocabulary. The ERD does not mark
``city_ref_id`` or ``tax_number`` required, so both are nullable.

``status`` — ASSUMPTION, not a silent fact:
    PART A provides no dedicated CustomerStatus vocabulary. This model assumes
    ``ACTIVE`` / ``INACTIVE``, matching the existing unspecified-status pattern
    in ``app_user`` / ``warehouse`` / ``carrier``. The CHECK makes that
    assumption explicit and localized.

``billing_address`` — placeholder type:
    No address-specific factory exists yet. ``description_type()`` is the
    closest existing factory, the same placeholder treatment as
    ``warehouse.address``; this is not a decision on a structured address
    representation.

``credit_limit_amount``:
    ``money_type()`` supplies ``NUMERIC(18, 4)``. It defaults to zero both in
    Python and on the server: a newly created customer has no credit extended
    until a positive limit is expressly assigned.

Soft delete:
    M8 is classified ``M + soft-deletable``. ``deleted_at`` is declared directly
    as a nullable timezone-aware ``TIMESTAMPTZ`` (NULL means not deleted),
    following the current M-class model pattern rather than introducing a new
    shared design decision. This implements the ERD's "cannot hard-delete"
    requirement for preserving historical invoices; it is not a separate,
    unimplemented rule.

Credit-limit validation depends on order and invoice state, so blocking a new
order that would exceed the limit is a service-layer cross-table/temporal rule,
not a row-local SQL constraint.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Customer`` opts its UAC ``version`` into SQLAlchemy
    optimistic locking.

Naming convention:
    ``code`` uses column-level ``unique=True`` → ``uq_customer_code``. FK and
    bounded-vocabulary constraint names use ``fk_index_name`` and
    ``ck_index_name`` respectively.

Column-type choices:

* ``code`` / ``tax_number`` — ``code_short_type()`` → ``VARCHAR(40)``.
* ``name`` — ``name_type()`` → ``VARCHAR(160)``.
* ``billing_address`` — ``description_type()`` → ``VARCHAR(255)``.
* ``credit_limit_amount`` — ``money_type()`` → ``NUMERIC(18, 4)``.
* ``type`` / ``status`` — ``state_token_type()`` → ``VARCHAR(16)``.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, fk_index_name
from database.types import (
    code_short_type,
    description_type,
    money_type,
    name_type,
    state_token_type,
)


class Customer(Base, UniversalAuditColumns):
    """``M8 — customer`` — independent customer Aggregate Root (M + soft-deletable)."""

    __tablename__ = "customer"
    __mapper_args__ = {"version_id_col": "version"}

    id: GuidPk = id_column()

    city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "city_ref.id",
            name=fk_index_name("customer", "city_ref_id", "city_ref"),
        ),
        nullable=True,
    )
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )
    billing_address: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )
    credit_limit_amount: Mapped[Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=Decimal("0"),
        server_default=sa_text("0"),
    )
    currency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "currency.id",
            name=fk_index_name("customer", "currency_id", "currency"),
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )
    tax_number: Mapped[str | None] = mapped_column(
        code_short_type(),
        nullable=True,
    )

    # Direct, opt-in soft-delete marker; NULL means not soft-deleted.
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
