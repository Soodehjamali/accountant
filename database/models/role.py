"""``R6 — role`` ORM model (RBAC role definitions).

Authority: ``06_ERD.md``, R6 → ``role``::

    R6 — role
    Purpose: RBAC definitions and role-permission mapping.
    PK: id
    FK: none
    Important fields: code (unique), name, description
    Unique: code
    Business constraints: none stated
    Classification: R/C

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``role`` is an editable reference/configuration table, so it
    uses UAC and opts its ``version`` column into SQLAlchemy optimistic locking.

Naming convention:
    ``code`` uses column-level ``unique=True``, which the shared metadata
    convention renders as ``uq_role_code``.

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)`` for a short controlled
  role code.
* ``name`` — ``name_type()`` → ``VARCHAR(160)`` for the role display name.
* ``description`` — ``description_type()`` → ``VARCHAR(255)`` for optional
  human-readable role detail.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import code_short_type, description_type, name_type


class Role(Base, UniversalAuditColumns):
    """``R6 — role`` — RBAC role definition (Classification: R/C)."""

    __tablename__ = "role"

    # Optimistic locking — activate the UAC ``version`` column as SQLAlchemy's
    # row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    id: GuidPk = id_column()

    # Short controlled role code (e.g. ADMIN, SALES_REP).
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # Human-readable role display name.
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # Optional role detail for administrators and operators.
    description: Mapped[str | None] = mapped_column(
        description_type(),
        nullable=True,
    )


__all__ = ["Role"]
