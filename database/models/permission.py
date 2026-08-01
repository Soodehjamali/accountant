"""``R7 — permission`` ORM model (RBAC permission definitions).

Authority: ``06_ERD.md``, R7 → ``permission``::

    R7 — permission
    Purpose: RBAC definitions and role-permission mapping.
    PK: id
    FK: none
    Important fields: code (unique), name, resource, action
    Unique: code
    Business constraints: none stated
    Classification: R

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``permission`` is an editable reference table, so it uses UAC
    and opts its ``version`` column into SQLAlchemy optimistic locking.

Naming convention:
    ``code`` uses column-level ``unique=True``, which the shared metadata
    convention renders as ``uq_permission_code``.

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)`` for a short controlled
  permission code.
* ``name`` — ``name_type()`` → ``VARCHAR(160)`` for the permission display
  name.
* ``resource`` / ``action`` — each uses ``code_short_type()`` → ``VARCHAR(40)``.
  They are short controlled RBAC tokens (for example, ``order`` and ``approve``),
  so the existing short-code factory is the narrowest semantic fit.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.types import code_short_type, name_type


class Permission(Base, UniversalAuditColumns):
    """``R7 — permission`` — RBAC permission definition (Classification: R)."""

    __tablename__ = "permission"

    # Optimistic locking — activate the UAC ``version`` column as SQLAlchemy's
    # row-version concurrency token.
    __mapper_args__ = {"version_id_col": "version"}

    id: GuidPk = id_column()

    # Short controlled permission code (e.g. ORDER_APPROVE).
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # Human-readable permission display name.
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # Controlled RBAC resource token (e.g. order).
    resource: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
    )

    # Controlled verb/action token for the resource (e.g. approve).
    action: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
    )


__all__ = ["Permission"]
