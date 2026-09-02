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


class ProductNotFoundError(LookupError):
    """Raised when a referenced ``product_id`` has no matching, non-deleted row."""

    def __init__(self, product_id: uuid.UUID) -> None:
        super().__init__(f"No product with id '{product_id}' exists.")
        self.product_id = product_id


class ProductInUseError(ValueError):
    """Raised when attempting to delete a product that is still referenced."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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


def _get_product_or_raise(session: Session, product_id: uuid.UUID) -> Product:
    product = session.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    ).scalar_one_or_none()
    if product is None:
        raise ProductNotFoundError(product_id)
    return product


def delete_product(session: Session, product_id: uuid.UUID) -> None:
    """Hard-delete a product if it is not referenced by any other records.

    Raises:
        ProductNotFoundError: if no non-deleted product with this ID exists.
        ProductInUseError: if the product is referenced by other records.
    """
    from sqlalchemy import func as sqlfunc

    product = _get_product_or_raise(session, product_id)

    # Check FK references from transactional / catalog tables.
    from database.models.order_line import OrderLine
    from database.models.invoice_line import InvoiceLine
    from database.models.return_line import ReturnLine
    from database.models.transfer_line import TransferLine
    from database.models.shipment_line import ShipmentLine
    from database.models.physical_count_line import PhysicalCountLine
    from database.models.stock_adjustment import StockAdjustment
    from database.models.stock_reservation import StockReservation
    from database.models.inventory_transaction import InventoryTransaction
    from database.models.product_image import ProductImage
    from database.models.product_serial import ProductSerial
    from database.models.product_lot import ProductLot
    from database.models.price_history import PriceHistory
    from database.models.purchase_price_history import PurchasePriceHistory
    from database.models.discount import Discount
    from database.models.uom_conversion import UomConversion

    checks = [
        (OrderLine, "product_id", "order lines"),
        (InvoiceLine, "product_id", "invoice lines"),
        (ReturnLine, "product_id", "return lines"),
        (TransferLine, "product_id", "transfer lines"),
        (ShipmentLine, "product_id", "shipment lines"),
        (PhysicalCountLine, "product_id", "physical count lines"),
        (StockAdjustment, "product_id", "stock adjustments"),
        (StockReservation, "product_id", "stock reservations"),
        (InventoryTransaction, "product_id", "inventory transactions"),
        (ProductImage, "product_id", "product images"),
        (ProductSerial, "product_id", "product serial records"),
        (ProductLot, "product_id", "product lots"),
        (PriceHistory, "product_id", "price history records"),
        (PurchasePriceHistory, "product_id", "purchase price history records"),
        (Discount, "product_id", "discounts"),
        (UomConversion, "product_id", "UoM conversions"),
    ]

    refs = []
    for model, col, label in checks:
        count = session.execute(
            select(sqlfunc.count()).select_from(model).where(
                getattr(model, col) == product_id
            )
        ).scalar_one()
        if count > 0:
            refs.append(f"{count} {label}")

    if refs:
        raise ProductInUseError(f"Cannot delete: still referenced by {', '.join(refs)}")

    session.delete(product)
    session.flush()


__all__ = [
    "DuplicateSkuError",
    "ProductNotFoundError",
    "ProductInUseError",
    "create_product",
    "delete_product",
    "get_product_by_sku",
    "list_products",
]
