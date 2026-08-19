"""``R1 — product_category`` ORM model (hierarchical product taxonomy).

Authority: ``06_ERD.md``, R1 → ``product_category``::

    R1 — product_category
    Purpose: Hierarchical product taxonomy (parent/child).
    PK: id
    FK: parent_category_id → product_category.id (nullable, self-ref)
    Important fields: code (unique), name, path (materialized path for fast subtree queries), level
    Unique: code
    Business constraints: no cyclic parent chain; cannot delete category with children or bound products
    Classification: R

The self-referential parent-chain and deletion rules are graph/cross-table
business invariants, respectively. They are enforced by the service layer, not
as row-local SQL constraints.

Audit-column family — ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. The ERD's universal audit policy applies to every editable
    R-class reference table. ``ProductCategory`` therefore uses UAC and opts
    its ``version`` column into SQLAlchemy optimistic locking.

Naming convention:
    ``code`` uses column-level ``unique=True``, which the shared metadata
    convention renders as ``uq_product_category_code``. The self-reference is
    explicitly named ``fk_product_category_parent_category_id_product_category_id``
    through ``fk_index_name``.

Column-type choices:

* ``code`` — ``code_short_type()`` → ``VARCHAR(40)`` for a short controlled
  category code.
* ``name`` — ``name_type()`` → ``VARCHAR(160)`` for the category display name.
* ``path`` — ``storage_key_type()`` → ``VARCHAR(512)``. A materialized path is
  a slash-delimited hierarchical key; the existing URI/storage-key width is the
  narrowest project factory that reasonably accommodates a variable-depth
  taxonomy without imposing an undocumented hierarchy-depth cap.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import fk_index_name
from database.types import code_short_type, name_type, storage_key_type


class ProductCategory(Base, UniversalAuditColumns):
    """``R1 — product_category`` — hierarchical product taxonomy (Classification: R)."""

    __tablename__ = "product_category"

    # Optimistic locking — activate the UAC ``version`` column as SQLAlchemy's
    # row-version concurrency token.
    @declared_attr
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.version}

    id: GuidPk = id_column()

    # Optional parent permits root categories; it references this same table.
    parent_category_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "product_category.id",
            name=fk_index_name(
                "product_category",
                "parent_category_id",
                "product_category",
            ),
        ),
        nullable=True,
    )

    # Short controlled category code.
    code: Mapped[str] = mapped_column(
        code_short_type(),
        nullable=False,
        unique=True,
    )

    # Human-readable category display name.
    name: Mapped[str] = mapped_column(
        name_type(),
        nullable=False,
    )

    # Slash-delimited, materialized subtree path.
    path: Mapped[str] = mapped_column(
        storage_key_type(),
        nullable=False,
    )

    # Zero-based or one-based hierarchy depth is a service-layer convention.
    level: Mapped[int] = mapped_column(
        nullable=False,
    )


__all__ = ["ProductCategory"]
