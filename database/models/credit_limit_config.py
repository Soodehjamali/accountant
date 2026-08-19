"""
credit_limit_config

Source: 06_ERD.md, PART D (Configuration Tables), entry C7:

    C7 -- credit_limit_config (often denormalized into customer; kept here
    if more granular per tier/region)
    Purpose: Optional per-segment credit limits beyond those on customer.
    Classification: C (optional)

*** IMPORTANT CAVEAT -- read before relying on this file verbatim ***

Unlike every other table built in this pass (audit_log/H6, kpi_snapshot/H10,
and the earlier approval_request/approval_history/notification), C7's ERD
entry above is the *entire* text the ERD gives it -- no Primary Key line, no
Foreign Keys line, no Important Fields, no Unique/Business Constraints,
unlike its C1-C6 siblings (e.g. C1 commission_config, C5 warehouse_assignment)
which all list PK/FK/fields/unique constraints explicitly.

07_DATABASE_SPEC.md's Part C/D (Configuration Tables) physical entry for C7
-- which is presumably where the actual column list, unique constraints, and
check constraints live, following the doc's own per-table format used for
every other table in this codebase -- could NOT be retrieved for this pass.
That section of the document sits before the "F.5 -- Fulfillment/Shipping"
heading, and every fetch attempt against the file (multiple token limits,
multiple extraction modes) consistently returned only the region from F.5
onward, never the earlier PART C/D content. audit_log (H6) and kpi_snapshot
(H10) were NOT affected by this -- both sit in the reachable F.10/F.16
region and are transcribed verbatim from the real spec.

Given that, the column list below is a **best-effort design**, not a spec
transcription:
  * Modeled directly on C1 (commission_config)'s pattern -- the closest
    sibling: nullable scoping FKs + an amount + effective_from/effective_to,
    since C7's own purpose line ("per-segment credit limits ... per
    tier/region") describes the same shape of problem commission_config
    solves for commission rates.
  * `customer_id`/`representative_id` scoping FKs are inferred from the
    task's own description ("سقف اعتبار مشتری/نماینده" -- customer/rep
    credit limit) and from `customer.credit_limit_amount` (M8) already
    existing as the base case this table overrides.
  * `city_ref_id` (regional scoping) and `customer_type` (tier scoping) are
    inferred from the ERD's "per tier/region" phrase and from CustomerType /
    city_ref already existing elsewhere in the schema for exactly that
    purpose.

Mixin choice: UniversalAuditColumns (UAC). Rationale: ERD Section 0.2 states
every table gets the universal audit columns by default, with append-only
history tables (Section 0.3) as the only carved-out exception; C7 is
Configuration (mutable), not History, so it follows the same default every
other C-table (C1-C6) follows.

RECOMMENDATION: paste 07_DATABASE_SPEC.md's actual C7 section (or the
PART C/D region generally) and this file will be corrected to match it
verbatim, the same way approval_request.py etc. were built from the real
spec text rather than inference.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, idx_index_name, uq_index_name

_TABLE = "credit_limit_config"


class CreditLimitConfig(Base, UniversalAuditColumns):
    """Optional per-segment credit limits beyond those on customer.

    ERD id: C7. Classification: C (configuration, optional/inferred design
    -- see module docstring's IMPORTANT CAVEAT).
    """

    __tablename__ = _TABLE
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    __table_args__ = (
        CheckConstraint(
            "credit_limit_amount >= 0",
            name=ck_index_name(_TABLE, "amount_nonneg"),
        ),
        # Mirrors C1 commission_config's uq_commission_config pattern:
        # one row per distinct scope combination per effective_from.
        Index(
            uq_index_name(_TABLE, "scope_effective_from"),
            "customer_id",
            "representative_id",
            "customer_type",
            "city_ref_id",
            "effective_from",
            unique=True,
        ),
        Index(idx_index_name(_TABLE, "customer_id"), "customer_id"),
        Index(idx_index_name(_TABLE, "representative_id"), "representative_id"),
    )

    id: GuidPk = id_column()

    # --- scoping (inferred; see module docstring) ---------------------------
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customer.id"),
        nullable=True,
    )
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("representative.id"),
        nullable=True,
    )
    # Tier scoping; reuses the CustomerType enum vocabulary (INDIVIDUAL /
    # CORPORATE) already used by customer.type.
    customer_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("city_ref.id"),
        nullable=True,
    )

    credit_limit_amount: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    currency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("currency.id"),
        nullable=False,
    )
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CreditLimitConfig id={self.id} customer_id={self.customer_id} "
            f"representative_id={self.representative_id} "
            f"amount={self.credit_limit_amount}>"
        )
