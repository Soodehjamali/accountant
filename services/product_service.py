"""Service layer for ``product`` (M1).

Authority for the business rules encoded here: ``06_ERD.md`` M1 and
``database/models/product.py``'s own docstring:

* ``sku`` is unique and, per the ERD, immutable once shipped against (that
  later "immutable after shipment" rule is out of scope here -- it needs the
  shipment/inventory services to exist first; this module only enforces
  "unique on create", the part that's meaningful today).
* ``status`` is one of ``ACTIVE`` / ``DISCONTINUED``; new products are always
  created ``ACTIVE``.
* Soft-deleted products (``deleted_at IS NOT NULL``) are excluded from
  ``list_products`` by default, mirroring the model's soft-delete contract.

As documented in ``services/__init__.py``, every function here takes an
already-open ``Session`` and never commits/closes it -- that's the caller's
job (desktop UI, script, or a future API endpoint).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.product import Product


class DuplicateSkuError(ValueError):
    """Raised when ``create_product`` is called with an SKU already in use."""

    def __init__(self, sku: str) -> None:
        super().__init__(f"A product with SKU '{sku}' already exists.")
        self.sku = sku


def create_product(
    session: Session,
    *,
    sku: str,
    name: str,
    base_uom_id: uuid.UUID,
    created_by: uuid.UUID,
    category_id: uuid.UUID | None = None,
    description: str | None = None,
) -> Product:
    """Create and return a new, ``ACTIVE`` product.

    Raises:
        DuplicateSkuError: if ``sku`` is already used by another product
          (checked explicitly here, rather than only relying on the DB's
          unique-constraint error, so callers get a clear, typed exception).
    """

    existing = session.execute(
        select(Product).where(Product.sku == sku)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateSkuError(sku)

    product = Product(
        sku=sku,
        name=name,
        description=description,
        category_id=category_id,
        base_uom_id=base_uom_id,
        status="ACTIVE",
        created_by=created_by,
    )
    session.add(product)
    session.flush()  # populate product.id / server defaults before return
    return product


def list_products(session: Session, *, include_discontinued: bool = True) -> list[Product]:
    """Return all non-soft-deleted products, ordered by SKU.

    Args:
        include_discontinued: when ``False``, excludes products whose
          ``status`` is ``DISCONTINUED`` (they remain visible historically
          per the model's own docstring, so the default here is ``True``).
    """

    stmt = select(Product).where(Product.deleted_at.is_(None))
    if not include_discontinued:
        stmt = stmt.where(Product.status == "ACTIVE")
    stmt = stmt.order_by(Product.sku)
    return list(session.execute(stmt).scalars().all())


def get_product_by_sku(session: Session, sku: str) -> Product | None:
    """Return the product with the given SKU, or ``None`` if not found."""

    return session.execute(
        select(Product).where(Product.sku == sku, Product.deleted_at.is_(None))
    ).scalar_one_or_none()


__all__ = [
    "DuplicateSkuError",
    "create_product",
    "get_product_by_sku",
    "list_products",
]
