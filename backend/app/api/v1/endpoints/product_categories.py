"""Product Category endpoints: ``/api/v1/product-categories``.

Read-only endpoint for the ``product_category`` (R1) reference catalog.
Any authenticated user can list product categories — this is reference data,
not a mutation-gated resource.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
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


PRODUCT_MANAGE_PERMISSION_CODE = "PRODUCT_MANAGE"
_require_product_manage = require_permission(PRODUCT_MANAGE_PERMISSION_CODE)


class ProductCategoryUpdateRequest(BaseModel):
    """Request body for ``PATCH /product-categories/{id}``."""

    name: str | None = None
    parent_category_id: uuid.UUID | None = None


@router.patch(
    "/product-categories/{category_id}",
    response_model=ProductCategoryResponse,
    summary="Update a product category",
)
def update_product_category(
    category_id: uuid.UUID,
    body: ProductCategoryUpdateRequest,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_product_manage),
) -> ProductCategoryResponse:
    """Patch-update a product category. Only non-None fields are applied."""
    cat = db.execute(
        select(ProductCategory).where(ProductCategory.id == category_id)
    ).scalar_one_or_none()
    if cat is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No product category with id '{category_id}' exists.",
        )

    if body.name is not None:
        cat.name = body.name
    if body.parent_category_id is not None:
        cat.parent_category_id = body.parent_category_id
    db.commit()
    db.refresh(cat)
    return cat


class ProductCategoryInUseError(ValueError):
    """Raised when attempting to delete a category that is still referenced."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@router.delete(
    "/product-categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a product category",
)
def delete_product_category(
    category_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_product_manage),
) -> None:
    """Hard-delete a product category if it is not referenced.

    Checks for: child categories (self-ref), products using this category.
    Returns HTTP 409 if still in use.
    """
    from database.models.product import Product

    cat = db.execute(
        select(ProductCategory).where(ProductCategory.id == category_id)
    ).scalar_one_or_none()
    if cat is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No product category with id '{category_id}' exists.",
        )

    refs = []

    # Check child categories (self-referential parent_category_id).
    child_count = db.execute(
        select(sqlfunc.count()).select_from(ProductCategory).where(
            ProductCategory.parent_category_id == category_id
        )
    ).scalar_one()
    if child_count > 0:
        refs.append(f"{child_count} child categories")

    # Check products referencing this category.
    product_count = db.execute(
        select(sqlfunc.count()).select_from(Product).where(
            Product.category_id == category_id
        )
    ).scalar_one()
    if product_count > 0:
        refs.append(f"{product_count} products")

    if refs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Cannot delete: still referenced by {', '.join(refs)}",
        )

    db.delete(cat)
    db.commit()


__all__ = ["router"]
