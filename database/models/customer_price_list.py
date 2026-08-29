"""``CustomerPriceList`` ORM model — assign which price list applies to a
customer, with effective date and precedence.

Business rule: ``02_SRS.md`` BR-P1 states:
    *"Final price resolved by priority: customer-specific > rep-tier >
    product default."*

This junction table implements the first level of that priority chain:
linking a customer to the price list that governs their pricing.

Pattern follows ``customer_rep_assignment`` (C6): time-bounded validity,
ranked by priority for overlapping windows, overlap enforcement at the
application/validation layer (not a DB constraint).

Classification: C (also acts as history).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import fk_index_name


class CustomerPriceList(Base, UniversalAuditColumns):
    """Assign which price list governs a customer's pricing.

    Follows the same time-window + priority pattern as
    ``CustomerRepAssignment`` (C6).  The highest-priority currently-active
    assignment is the customer's effective price list.
    """

    __tablename__ = "customer_price_list"

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
            name=fk_index_name("customer_price_list", "customer_id", "customer"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------ price_list_id
    price_list_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "price_list.id",
            name=fk_index_name("customer_price_list", "price_list_id", "price_list"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- effective_from
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ---------------------------------------------------------------- effective_to
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------------------------ priority
    priority: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )

    # No __table_args__: overlap rule enforced by app/validation,
    # same as customer_rep_assignment (C6).


__all__ = ["CustomerPriceList"]
