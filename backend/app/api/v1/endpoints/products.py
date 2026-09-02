"""Product endpoints: ``POST /products``, ``GET /products``, ``GET /products/{sku}``.

The first Catalog-domain (M1) endpoints in this backend -- everything here
is a thin HTTP wrapper around ``services.product_service``, per this
project's layering rule (``services/__init__.py``'s docstring): business
rules live in ``services/``, never duplicated here. Mirrors the pattern
already established by ``app/api/v1/endpoints/auth.py``.

Mutating endpoints (``POST /products``) are gated behind the
``PRODUCT_MANAGE`` permission (via ``require_permission``, mirroring how
``endpoints/customers.py`` gates behind ``CUSTOMER_MANAGE``); reads
require only an authenticated caller, matching the convention every other
domain follows for read endpoints.

Products are master/catalog data (global, not representative-scoped), so
no representative scope enforcement is applied.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.product import ProductCreateRequest, ProductListResponse, ProductResponse, ProductUpdateRequest
from database.models.app_user import AppUser
from services import product_service

router = APIRouter(prefix="/products", tags=["products"])

PRODUCT_MANAGE_PERMISSION_CODE = "PRODUCT_MANAGE"
_require_product_manage = require_permission(PRODUCT_MANAGE_PERMISSION_CODE)


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
)
def create_product(
    body: ProductCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_product_manage),
) -> ProductResponse:
    """Create a new, ``ACTIVE`` product.

    Returns HTTP 409 if ``sku`` is already in use (mirrors
    ``product_service.DuplicateSkuError`` -- 409 Conflict is the correct
    status for "the request is valid but collides with existing state",
    distinct from a validation error).
    """

    try:
        product = product_service.create_product(
            db,
            sku=body.sku,
            name=body.name,
            description=body.description,
            base_uom_id=body.base_uom_id,
            category_id=body.category_id,
            created_by=current_user.id,
        )
    except product_service.DuplicateSkuError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=ProductListResponse, summary="List products")
def list_products(
    include_discontinued: bool = True,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> ProductListResponse:
    """Return all non-soft-deleted products, ordered by SKU.

    ``include_discontinued`` (query param, default ``True``) mirrors
    ``product_service.list_products``'s own parameter of the same name.
    """

    products = product_service.list_products(
        db, include_discontinued=include_discontinued
    )
    return ProductListResponse(items=list(products))


@router.get("/{sku}", response_model=ProductResponse, summary="Get a product by SKU")
def read_product(
    sku: str,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> ProductResponse:
    """Return the product with the given SKU, or HTTP 404 if none exists."""

    product = product_service.get_product_by_sku(db, sku)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No product found with SKU '{sku}'.",
        )
    return product


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Update a product",
)
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_product_manage),
) -> ProductResponse:
    """Patch-update a product. Only non-None fields are applied."""
    try:
        product = product_service.update_product(
            db,
            product_id,
            updated_by=current_user.id,
            name=body.name,
            description=body.description,
            base_uom_id=body.base_uom_id,
            category_id=body.category_id,
            status=body.status,
        )
    except product_service.ProductNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(product)
    return product


@router.delete(
    "/{product_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a product",
)
def delete_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_product_manage),
) -> None:
    """Hard-delete a product if it is not referenced by any other records.

    Returns HTTP 409 if the product is still in use.
    """
    try:
        product_service.delete_product(db, product_id)
    except product_service.ProductNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except product_service.ProductInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()


__all__ = ["router"]
