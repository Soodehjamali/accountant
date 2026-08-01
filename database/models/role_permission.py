"""``R8 — role_permission`` ORM model (RBAC role-permission junction).

Authority: ``06_ERD.md``, R8 → ``role_permission``::

    R8 — role_permission (J junction)
    Purpose: RBAC definitions and role-permission mapping.
    PK: composite (role_id, permission_id)
    FK: role_id → role.id, permission_id → permission.id
    Important fields: none stated
    Unique: (role_id, permission_id)
    Business constraints: none stated
    Classification: J

The ERD explicitly lists both the composite primary key and a UNIQUE constraint
on the identical ``(role_id, permission_id)`` pair. The UNIQUE constraint is
redundant in relational terms because the primary key already prohibits
duplicates, but it is retained as ``uq_role_permission_role_id_permission_id``
as an explicit, specification-mandated constraint rather than silently dropped.

Audit-column family — ``AppendOnlyAuditColumns`` (AAC):
    ``created_at`` / ``created_by`` only. ``database.mixins`` scopes AAC to
    immutable / append-only rows and deliberately omits update timestamps and
    optimistic-lock columns. The ERD does not otherwise specify an audit family
    for this J-class mapping; AAC is used because each row records a role grant
    as an insertion-only association, matching the linked-at / created-at-only
    shape described for J1 ``invoice_order``. No surrogate ``id`` is added:
    the ERD requires this junction's two foreign keys to be its composite PK.

Naming convention:
    Each foreign key is explicitly named through ``fk_index_name``. The
    specification-mandated pair UNIQUE is explicitly named through
    ``uq_index_name`` with ``composite_descriptor``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.mixins import AppendOnlyAuditColumns
from database.naming import composite_descriptor, fk_index_name, uq_index_name


class RolePermission(Base, AppendOnlyAuditColumns):
    """``R8 — role_permission`` — RBAC role-permission junction (Classification: J)."""

    __tablename__ = "role_permission"

    # Composite identity: a role may receive each permission at most once.
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "role.id",
            name=fk_index_name("role_permission", "role_id", "role"),
        ),
        primary_key=True,
        nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "permission.id",
            name=fk_index_name(
                "role_permission",
                "permission_id",
                "permission",
            ),
        ),
        primary_key=True,
        nullable=False,
    )

    __table_args__ = (
        # Redundant with the composite PK but explicitly required by the ERD.
        UniqueConstraint(
            "role_id",
            "permission_id",
            name=uq_index_name(
                "role_permission",
                composite_descriptor(("role_id", "permission_id")),
            ),
        ),
    )


__all__ = ["RolePermission"]
