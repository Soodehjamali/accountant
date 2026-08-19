"""``R14 — carrier`` ORM model (shipping carrier reference).

Authority: ``06_ERD.md``, F.14 → ``R14 — carrier``::

    R14 — carrier
    Purpose: Shipping carrier reference.
    PK: id
    FK: none
    Important fields: code (unique), name, contact_phone,
                      tracking_url_template, status (ACTIVE/INACTIVE)
    Unique: code
    Business constraints: cannot deactivate a carrier that has any shipment
                          in state IN_TRANSIT
    Classification: R

The ERD specifies the carrier's own bounded status vocabulary.  The operational
cross-table deactivation rule belongs to shipment/service-layer transition
validation and cannot be expressed as a row-local SQL CHECK on this table.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``.  The ERD's universal audit policy applies to every editable
    R-class reference table.  ``Carrier`` therefore uses UAC and opts its
    ``version`` column into SQLAlchemy optimistic locking.

Naming convention:
    ``code`` uses column-level ``unique=True``, which the shared metadata
    convention renders as ``uq_carrier_code``.  The ERD-stated status vocabulary
    is constrained by ``ck_carrier_status_values`` via ``ck_index_name``.

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)`` for a short controlled
  reference code.
* ``name`` — ``name_type()`` → ``VARCHAR(160)`` for the carrier display name.
* ``contact_phone`` — raw ``String(25)``. No existing helper models formatted,
  internationally dialable telephone numbers. E.164 permits 15 digits; this
  width retains room for ``+``, spacing/punctuation, and a short extension.
* ``tracking_url_template`` — ``storage_key_type()`` → ``VARCHAR(512)``. Its
  URI-oriented width is an appropriate existing fit for long URL templates such
  as ``https://carrier.example.com/track/{tracking_number}``.
* ``status`` — ``state_token_type()`` → ``VARCHAR(16)``; bounded to the ERD's
  explicit ``ACTIVE`` / ``INACTIVE`` values by a CHECK constraint.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name
from database.types import code_short_type, name_type, state_token_type, storage_key_type


class Carrier(Base, UniversalAuditColumns):
    """``R14 — carrier`` — shipping carrier reference (Classification: R)."""

    __tablename__ = "carrier"

    # Optimistic locking — activate the UAC ``version`` column as SQLAlchemy's
    # row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    id: GuidPk = id_column()

    # Short controlled carrier code (e.g. DHL, FEDEX).
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # Human-readable carrier display name.
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # Formatted international telephone number. E.164 has 15 digits; 25 chars
    # also accommodates +, separators, and a concise extension. No existing
    # project helper has this telephone-specific semantic.
    contact_phone: Mapped[str] = mapped_column(
        String(25),
        nullable=False,
    )

    # Carrier tracking URL template; the URI/storage-key helper's 512-char
    # capacity accommodates domain/path/query templates without a new width.
    tracking_url_template: Mapped[str] = mapped_column(
        storage_key_type(),
        nullable=False,
    )

    # The ERD explicitly limits this state to ACTIVE / INACTIVE.
    status: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name=ck_index_name("carrier", "status_values"),
        ),
    )


__all__ = ["Carrier"]
