"""Product Category endpoints: ``/api/v1/product-categories``.

Read-only endpoint for the ``product_category`` (R1) reference catalog.
Any authenticated user can list product categories — this is reference data,
not a mutation-gated resource.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from database.models.app_user import AppUser
from database.models.product_category import ProductCategory

router = APIRouter(tags=["product-categories"])


class ProductCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    parent_category_id: uuid.UUID | None
    level: int


class ProductCategoryListResponse(BaseModel):
    items: list[ProductCategoryResponse]


@router.get(
    "/product-categories",
    response_model=ProductCategoryListResponse,
    summary="List product categories",
)
def list_product_categories(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> ProductCategoryListResponse:
    """Return all product categories ordered by hierarchy.

    Any authenticated user can read this — it's reference data shared
    across multiple domains (products, discounts, commissions, etc.).
    """
    rows = db.execute(
        select(ProductCategory).order_by(ProductCategory.level, ProductCategory.code)
    ).scalars().all()
    return ProductCategoryListResponse(
        items=[ProductCategoryResponse.model_validate(r) for r in rows]
    )


__all__ = ["router"]
