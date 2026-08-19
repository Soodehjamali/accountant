"""Request/response schemas for the product endpoints (``/api/v1/products``).

Kept independent of the SQLAlchemy ``Product`` ORM model (per ``app/
schemas/__init__.py``'s own docstring), mirroring the pattern already
established by ``app/schemas/auth.py``.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProductCreateRequest(BaseModel):
    """Request body for ``POST /products``.

    Mirrors ``services.product_service.create_product``'s own parameters.
    ``base_uom_id`` is required (matches ``Product.base_uom_id``, which is
    ``NOT NULL`` on the model); ``category_id`` and ``description`` are
    optional, matching the model's nullable columns.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sku": "SKU-0004",
                "name": "Steel Washer 10mm",
                "description": "Matching 10mm steel washer.",
                "base_uom_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "category_id": None,
            }
        }
    )

    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=255)
    base_uom_id: uuid.UUID
    category_id: uuid.UUID | None = None


class ProductResponse(BaseModel):
    """Response body for a single product -- used by both create and list/get.

    A deliberately narrow projection of ``Product`` (mirrors
    ``CurrentUserResponse``'s own note on this in ``app/schemas/auth.py``):
    exposes the fields a client needs to display/manage a product, not a
    full 1:1 column dump (e.g. ``created_by``/``version`` audit plumbing is
    omitted here).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    description: str | None
    status: str
    category_id: uuid.UUID | None
    base_uom_id: uuid.UUID
    is_lot_tracked: bool
    is_serial_tracked: bool
    is_perishable: bool


class ProductListResponse(BaseModel):
    """Response body for ``GET /products`` -- a simple wrapped list.

    Wrapped (rather than a bare JSON array) so pagination metadata (e.g. a
    future ``total``/``next_cursor``) can be added later without an
    incompatible, breaking response-shape change for existing clients.
    """

    items: list[ProductResponse]


__all__ = ["ProductCreateRequest", "ProductListResponse", "ProductResponse"]
